# k59 批次报告：`Retriever` 空集合自愈的**写路径**根因（数据安全）

**分支**：`k59-retriever`（worktree `/home/a/k59-wt`，起点 `55ea3f7`）
**提交**：`k59-retriever` 分支 HEAD（本批一次提交；精确哈希见 `git log -1 --oneline`）
**一句话**：自愈本是**读路径**的降级便利，却被写方法经 `collection` 属性继承，
导致「生产 persist_dir + 一个不存在的集合名」写入时静默落到 `fortune_books_v2`。
本批按 **A+B 纵深防御**修根因：写路径**不复用**自愈结果（已自愈实例写入抛
`SelfHealWriteRefused`），自愈**只在读方法生效**（写目标 = 调用方显式集合名）。
读路径行为逐字未变。生产库零变化（只读指纹 before/after 完全一致）。

---

## 1. 设计意图：自愈的本意是**读**，写路径是「继承来的副作用」

### 1.1 自愈为什么存在（k24）

`_ensure_non_empty_collection()`（`src/rag/retriever.py`）的 docstring 与
`src/book_categories.py` 的事故背景写得很明确：线上 8 项能力日志反复出现
「Collection 'fortune_books' exists but is empty … legacy RAG fallback will
return empty results」，根因是**配置踩空**（`load_settings` 不读
`embedding_collection` → 取默认 `fortune_books`，或 settings.yaml 写了空集合
`fortune_v6`）+ 类目错配。后果是 refs=0 → **LLM 凭记忆编造引文**。自愈就是
针对这一条链的止血：**集合为空/缺失/不可用时，读回落权威库 `fortune_books_v2`**
（`KNOWN_EMPTY_COLLECTIONS` 免探测；其余按 `count()==0`；权威库也空才算失败）。

意图的旁证（都在代码里）：
- 术语全是**检索侧**语言：「无检索价值 → 自动改用权威古籍库」「首个检索若发生
  在低 S 日志窗口」「配置写错但检索仍可用」（k28/k31 注释、warning 文案）；
- 触发源是**服务配置**（`embedding_collection` / `EMBEDDING_COLLECTION`），不是
  入库脚本的目标集合；
- 自愈事件表 `self_heal_events()` 是给**运维巡检**看的（读路径观测）。

**从未有过「写也要回落」的设计**——写路径是被 `collection` 属性（读写共用一个
访问器）顺带继承的。k55 的险情正是这一继承的后果，实现者在 `add_chunks` 前发现
才没落到生产。

### 1.2 两条路径的期望语义（本批确立并锁死）

| | **读**（`collection` / `search` / `count` / `collection_name`） | **写**（`add_chunks` / `writable_collection`） |
|---|---|---|
| 目标集合来源 | 配置/构造的集合名，**可被自愈改写** | 调用方**显式**集合名，绝不改写 |
| 集合缺失/为空 | 回落权威库（`BOOKS_COLLECTION`）并 warning + 事件留痕；权威库也空 → 保持原名、事件 `healed_to=None`、读返回空（调用方按「未检索到」处理） | **不检测、不回落**：目标缺失就按显式名字新建并写入（「新建集合再写」是明确意图）；同时若同目录权威集合有数据 → warning 留痕（高危形态，绝不静默） |
| 已自愈实例再被使用 | 正常（读的就是权威库） | **抛 `SelfHealWriteRefused`**（拒绝把数据写进权威生产集合） |
| 是否可静默 | 有 warning + `self_heal_events()` 可查（k28 起「绝不静默」） | 绝不静默：改写则抛错，高危形态则 warning |

一句话：**读可以降级，写不允许改写。**

---

## 2. 修法（A+B 纵深防御）与依据

`src/rag/retriever.py`：

1. **A（断言未被改写 → 抛错）**：`self._self_healed_to` 由 `_ensure_non_empty_collection`
   在改写集合名时登记；写路径 `writable_collection` 一旦看到它非空即抛
   `SelfHealWriteRefused`（新增异常，`RuntimeError` 子类，带 `requested` /
   `healed_to` / `persist_dir` 三个属性 + 可读消息）。另记
   `self._requested_collection_name`（调用方原始意图，自愈不动它）作为诊断口径。
2. **B（自愈只作用于读方法）**：写路径 `writable_collection` **不调用**
   `_ensure_non_empty_collection()`，直接按 `self._collection_name` 取/建句柄；
   并且**不复用**读路径的 `self._collection` 句柄缓存（那是读语义句柄），也不
   写回该缓存 —— 读路径句柄语义与 k59 前**逐字一致**（`_collection is None` 才取）。
3. **配套（绝不静默）**：`_warn_if_write_target_would_self_heal()` —— 写目标恰是
   「会触发自愈」的形态（已知空集合 / `count()==0` / 不可用，且权威集合有数据）
   时记一条 warning（同实例同集合名只提示一次），**只提示，不改写目标**。
4. `add_chunks` 改为用 `writable_collection`（写前一次性判定），docstring 写明
   语义；`collection` 属性的 docstring 明确「读语义，不得用于写入」。

### 2.1 为什么选 A+B 而不是「写目标缺失就一律拒绝」（brief 的 A/B/C 对照）

- brief 的 **B** 原文允许两种修法：「写方法走**显式集合名**或抛错」。本批两种
  都实现了 —— 未自愈的实例走显式集合名（B），已自愈的实例抛错（A）。
- **不选**「写目标缺失/为空即拒绝（除非显式 opt-in）」的原因：仓库里有**既有
  合法用法**依赖「按显式名新建集合再写」——`scripts/ingest_new_data.py --collection`、
  `scripts/ingest_manual_texts.py --collection`、`scripts/rebuild_chroma_v2.py
  --target-collection`（三者都在生产目录里按 `--collection` 建新集合），以及
  k55 自己的 `scripts/k55_dream/index_corpus.py`（独立目录 + 显式绕过）。一律拒绝
  会**打断这些脚本的既定契约**，并把「新建集合」这一明确意图误判为错误；而根因
  （写入目标被静默改写）用 A+B 已彻底消除。若控制方仍希望「生产目录里新建集合
  必须先显式 opt-in」，那是脚本侧/策略侧的下一层红线（现在已由
  `index_corpus.py` 的目录红线覆盖），本批不擅自改变既有脚本契约。
- 高危形态仍**不静默**（第 3 条 warning），所以「本该警告却没警告」这一面也补上了。

### 2.2 相邻修复（同类写路径一并收口）

写路径不止 `add_chunks` 一处：`retriever.collection.upsert(...)` 这种**经读属性
写入**的既有脚本同样继承了自愈。已把 3 个既有调用点迁到 `writable_collection`：

- `scripts/batch_rag.py`、`scripts/ingest_github_repos.py`、
  `scripts/ingest_extracted_texts.py`（各 1 行 + 注释；读用 `collection`、写用
  `writable_collection`，契约统一）。

`add_chunks` 是 `src/` 内唯一写方法（生产服务不写库，仅离线入库脚本调用），
因此**零生产路径风险**。

---

## 3. 改前/改后对照（危险路径：复现 → 被拦）

全部实验在 `/dev/shm` 的**生产库副本**（`cp -a /home/a/data/vectordb_v2`，27,115
条 / 486MB）上跑；生产库只读（见 §5）。工具：`scripts/k59/replay_incident.py`
（`--code legacy` 逐字复刻改前写路径 `self.collection.upsert`；`--code guarded`
走修后 `add_chunks`；`--code guarded_after_read` 先读一次再写）。

| # | 代码 / 路径 | 请求集合 | 实际生效集合 | 处置 | 权威集合（副本） |
|---|---|---|---|---|---|
| ① | **改前**（55ea3f7 原样，`legacy`） | `k59_replay_new` | **`fortune_books_v2`** | `wrote`（**无报错**） | **27,115 → 27,116** `meta_md5 23be3a808bc7 → c6beb5943781` **[已变化]** |
| ② | 改后（`guarded`） | `k59_replay_new2` | `k59_replay_new2` | `wrote` | 27,115 → **27,115** `23be3a808bc7 → 23be3a808bc7` **[零变化]** |
| ③ | 改后（`guarded_after_read`，读自愈后再写） | `k59_replay_after_read` | `fortune_books_v2` | **`raised SelfHealWriteRefused`** | 27,115 → **27,115** **[零变化]** |
| ④ | 改后 + **绕过**写 API 直写读属性（残留风险见 §6） | `k59_replay_new` | `fortune_books_v2` | `wrote` | 27,115 → 27,116 **[已变化]** |

① 是 k55 险情的**逐条复现**（写入静默落到权威集合，只有一条集合配置 warning）；
② 证明根因修掉：**写落到显式集合本身**，权威集合零变化，并留 warning
「写入目标集合 'k59_replay_new2' 不可用: … 而权威集合 'fortune_books_v2' 有数据：
本次写入落到 'k59_replay_new2' 本身（写路径不使用自愈结果，k59）」；
③ 证明严格拒绝路径（异常消息含 `请求 'k59_replay_after_read' → 实际
'fortune_books_v2'，persist_dir=/dev/shm/k59/replica_guarded`）。

### 3.1 「改前失败」夹具（测试级）

把本批新增测试文件原样放到**改前代码树**（`git archive 55ea3f7` → `/dev/shm/k59_old`）
并只补一个符号垫片（改前没有 `SelfHealWriteRefused` 这个类，补上空类以便文件可
**加载**；被测的 Retriever 逻辑仍是改前的）：

```
11 failed, 1 passed, 1 skipped
危险夹具首条断言（安全性质）：
  AssertionError: 权威集合必须纹丝不动（改前：静默 +1 条污染）
  assert (7, '0648c9c0…') == (5, 'a0965d14…')     # 5 → 7：2 个 chunk 静默写进权威集合
```

同一个文件在改后代码上：**13 passed**。

---

## 4. 测试与回归数字

**新增**：`tests/test_k59_retriever_write_guard.py`（13 条，全部真实 chroma /
零网络 / 零模型，桩 embedder）：

1. 危险夹具（生产形态目录 + 缺失集合名）：权威集合指纹必须零变化；抛错或落显式集合皆算通过
2. 生产副本重放（`K59_PROD_REPLICA` 门控）：副本权威集合 sqlite 指纹零变化
3. 读自愈后写入必须抛 `SelfHealWriteRefused`（含异常属性校验 + 两处集合都未被写）
4. 配置指向历史空集合（`fortune_v6`）→ 读自愈后写入同样被拒
5. 读路径自愈保形：回落 + 检索可用 + warning + 事件表可查
6. 「存在但为空」的集合读路径仍自愈（k24 设计未改）
7. 正常写入不受影响（显式集合名 / 绑定名 / 无自愈改写）
8. 新建集合：落显式集合 + warning 留痕 + 只提示一次
9. 写路径不触发自愈（`_collection_checked` 保持 False、无自愈事件）
10. 重复写入幂等（指纹前后一致）
11. 脚本式 `_collection_name` 显式改名的既有用法照旧
12. 自愈失败（权威库也不存在）不阻断写入
13. 读路径不替换外部注入的 `_collection` 句柄（回归锁，见 §7）

**命令与结果**

```bash
# 新增用例（含生产副本重放）
TMPDIR=/dev/shm K59_PROD_REPLICA=/dev/shm/k59/replica_pytest nice -n 10 \
  python3 -m pytest tests/test_k59_retriever_write_guard.py -q
# → 13 passed

# 邻接回归（retriever / 自愈 / 出处契约 相关 8 个文件）
TMPDIR=/dev/shm nice -n 10 python3 -m pytest tests/test_rag.py \
  tests/test_k24_ref_content_crash.py tests/test_k26_sentinel_title.py \
  tests/test_k28_minors.py tests/test_k30_solar_projection.py \
  tests/test_k31_self_heal_counter.py tests/test_k38_e6_red_fixes.py \
  tests/test_k59_retriever_write_guard.py -q
# → 215 passed, 1 skipped（skip = 未设 K59_PROD_REPLICA 的副本用例）
```

**既有断言一条未改宽、未修改**；全量 pytest 未跑（按纪律由控制方统一安排）。
本批不涉及 LLM，未使用任何 LLM（免费 glm-4-flash 规则无从触发）。

---

## 5. 生产零变化证据（只读指纹）

**只读做法**：**不**构造 `chromadb.PersistentClient`（客户端初始化可能触发迁移
写盘），指纹一律走 `sqlite3 connect(file:…?mode=ro)` 直读 `chroma.sqlite3`——
工具 `scripts/k59/prod_fingerprint.py`。生产库 `fortune_books_v2` 在实验前后：

| 字段 | before | after |
|---|---|---|
| count | 27115 | 27115 |
| meta_rows | 108460 | 108460 |
| meta_md5 | `23be3a808bc7d1b3f6d1a2a1152a5cf6` | 同左 |
| ids_md5 | `9ea7d255044f12c1a4ee6704efdd7d8f` | 同左 |
| emb_rows | 27115 | 27115 |
| vec_header_md5 / vec_index_metadata_md5 | `04ffc7b77dee64839c19ca14054214af` / `cc1000e074ad4caf01df1a040f4c1c11` | 同左 |
| vec_files 大小 | data_level0.bin 112948704 / index_metadata.pickle 1097900 / length.bin 106656 / link_lists.bin 230348 / header.bin 100 | 同左 |

`before == after` → **True**（两个 JSON 逐字节相同）。条数 **27,115 与 brief 给出
的基线一致**。

> **诚实披露（指纹口径）**：brief 给的 MD5 基线是 `abc00d5b43863c2fe67e65556361ee74`，
> 我用 8 种常见口径（TSV/JSON/自然序/带 id/仅 id/仅文档…）都没能复现出该值，
> 也没在部署仓库里找到生成它的工具或脚本，故**无法确认控制方的口径**。本批给出
> 的是**自己文档化、可复现的口径**（脚本 docstring 写明），并以此做前后对照；
> 条数 27,115 与「权威集合未被写入」这一结论不受口径影响（§3 的副本对照是同一
> 口径下的 27,115 → 27,116，改后 27,115 → 27,115）。

---

## 6. 残留风险与控制方需要知道的事

1. **写 via 读属性仍可被改写**（表 3-④ 实证）：任何调用方绕过 `add_chunks` /
   `writable_collection`、直接 `retriever.collection.upsert(...)`，仍会写到自愈后
   的集合。根因是 `collection` 属性返回裸 chroma 集合对象，库层无法拦截其方法。
   本批已：①docstring 明示「读语义，不得写入」；②把仓库内 3 个既有调用点迁走
   （§2.2）。**建议**：控制方考虑后续批次加一条约定/静态检查
   （`\.collection\s*\.\s*(upsert|add|delete…)` 视为违规），或给 `collection`
   包一层只读代理（会触及检索器类型与既有测试的注入用法，本批不擅自做）。
2. **目录级红线仍在脚本侧**：`Retriever` 不知道「哪些目录是生产」。写路径现在
   会忠实按显式集合名在**任何**目录新建集合并写入（这正是 B 语义）。k55 的
   `index_corpus.py` 已自带目录红线（`--vectordb` == 生产目录即 `SystemExit`），
   建议所有入库脚本沿用；若要库层强制，需要控制方定策略（见 §2.1）。
3. **k55 脚本的绕过现在冗余**：`scripts/k55_dream/index_corpus.py` 里的
   `retriever._collection_checked = True` 是为绕开写路径自愈而加，根因修好后
   **不再需要**；本批未动它（避免扩面），仅此说明——留着无害（其集合在独立目录，
   自愈本就不会改写）。
4. **未改**：检索质量/排序逻辑、embedding 模型、集合命名约定、读路径任何行为。

---

## 7. 诚实披露（过程）

1. **第一版设计有回归，被邻接用例抓到并已修复**：我最初让读路径的句柄缓存「按
   集合名重绑」（`_collection_bound_name`），这打破了 k26
   `tests/test_k26_sentinel_title.py` 的既有用法（测试直接注入 `_collection` fake
   集合，读属性会把它换成真实集合）→ 4 个既有用例失败。已改为：**读路径句柄
   语义逐字不变**（`_collection is None` 才取），写路径自己取句柄、绝不复用/改写
   读缓存；并补了回归用例（新测试第 13 条）。邻接跑测抓 bug 的价值就在这里，
   特此留痕。
2. **未跑全量 pytest**（按纪律），只跑了本批新用例 + 8 个邻接文件（215 passed）。
3. **生产指纹口径无法与 brief 的 `abc00d5b…` 对齐**（见 §5 披露），本批以自报
   口径 + 条数一致性 + 副本对照作为证据。
4. **副本实验规模**：`/dev/shm` 上 4 份生产库副本（各 486MB）+ 改前代码树
   `/dev/shm/k59_old`，实验后**未删除**（便于控制方复核）；复核完可
   `rm -rf /dev/shm/k59 /dev/shm/k59_old`（约 2GB tmpfs）。生产库只读访问。
5. **`_seed` 预置方式**：测试沙箱预置「已有生产数据」用 chroma 客户端 +
   `_AppEmbeddingFunction`（不写 EF 会与 Retrieval 打开时的 EF 冲突），与生产
   集合形态一致（生产 3 个集合的 `config_json_str` 为 NULL，不会触发 EF 冲突）。

---

## 8. 文件清单与复现命令

**改动**
- `src/rag/retriever.py`：新增 `SelfHealWriteRefused` + `writable_collection` +
  `self_healed_to` + `_open_collection` + 写目标高危形态 warning；`add_chunks`
  改写路径；`collection` docstring 标注读语义；自愈处登记 `_self_healed_to`。
- `scripts/batch_rag.py` / `scripts/ingest_github_repos.py` /
  `scripts/ingest_extracted_texts.py`：写调用点迁到 `writable_collection`。

**新增**
- `tests/test_k59_retriever_write_guard.py`（13 条，含生产副本重放门控用例）
- `scripts/k59/prod_fingerprint.py`（生产库只读指纹工具）
- `scripts/k59/replay_incident.py`（k55 险情重放：legacy / guarded /
  guarded_after_read；内置红线：`--replica` 指向生产目录即拒绝执行）

**复现**

```bash
# 沙箱副本（绝不指生产）
mkdir -p /dev/shm/k59 && cp -a /home/a/data/vectordb_v2 /dev/shm/k59/replica_legacy
# 改前（55ea3f7 原样树）
mkdir -p /dev/shm/k59_old && (cd /home/a/k59-wt && git archive 55ea3f7) | tar -x -C /dev/shm/k59_old
mkdir -p /dev/shm/k59_old/scripts/k59 && cp /home/a/k59-wt/scripts/k59/*.py /dev/shm/k59_old/scripts/k59/
cd /dev/shm/k59_old && TMPDIR=/dev/shm nice -n 10 python3 scripts/k59/replay_incident.py \
  --replica /dev/shm/k59/replica_legacy --code legacy        # → 27115 → 27116（复现）
# 改后
cd /home/a/k59-wt && TMPDIR=/dev/shm nice -n 10 python3 scripts/k59/replay_incident.py \
  --replica /dev/shm/k59/replica_guarded --code both        # → 零变化 + 抛错路径
# 生产指纹（只读）
python3 scripts/k59/prod_fingerprint.py
```

**提交**：`k59-retriever` 分支（HEAD），仅提交、未合并、未推送、未重启服务、未碰生产。

---

## r2（控制方追加）：残留口子收口 —— 读属性直写这条复发面

### R2-1 仓库级静态守卫（已做）

新增 `tests/test_k59_collection_write_scan.py`（11 条）：

- **扫描面**：`src/**/*.py` + `scripts/**/*.py`（排除 `tests/`、`.git`、
  `__pycache__`、`node_modules`、`miniprogram/`）；注释行不扫，docstring/字符串照扫。
  实测扫描 **349 个文件**（src 207 + scripts 142），下限断言 `MIN_SCANNED_FILES=200`
  防「改窄 glob 让测试变绿」。
- **规则**：`\.collection\s*\.\s*(upsert|add|update|delete|modify)\b` 命中即失败；
  规则里的写方法集合与运行时只读包装**同源**（`COLLECTION_WRITE_METHODS`，单一事实源，
  由 `test_write_method_names_match_runtime_guard` 反向锁住）。
- **命中情况**：**原始命中 2 条，全部在 `scripts/k59/replay_incident.py`**（本批的
  「改前路径取证工具」：一次是可执行的 legacy 复刻调用，一次是 docstring 引述），
  已按 k52 口径白名单登记（逐条 `reason`）+ **反向锁**：
  ① 白名单条目必须真实在压制命中，否则判为过期条目（`test_whitelist_entries_have_reasons_and_are_used`）；
  ② 白名单只许覆盖该取证工具（`test_whitelisted_file_is_the_evidence_tool_only`），
  `src/` 出现任何白名单即失败。
- **迁移清单**：r1 已把 3 个真实调用点迁到 `writable_collection`
  （`scripts/batch_rag.py`、`scripts/ingest_github_repos.py`、
  `scripts/ingest_extracted_texts.py`）；本 r2 扫描确认 **`src/` 与业务脚本 0 命中、
  0 迁移遗留** → 无需再迁。
- 另附机制自检（合成命中必须判红、读方法 `get/count` 不得误报）与参数化兜底
  （5 个写方法名逐个在规则内）。

### R2-2 库层强制（**做了**）

`Retriever.collection` 现在返回**只读包装** `_ReadOnlyCollection`：
`__getattr__` 逐字透传 `query`/`get`/`count`/`name`/…，命中
`COLLECTION_WRITE_METHODS` 的写方法抛 `ReadPropertyWriteRefused`（`RuntimeError`
子类，带 `method`/`collection_name` + 指引 `add_chunks(...)` / `writable_collection.upsert(...)`）。
`writable_collection` 与 `add_chunks` 返回**裸** chroma 句柄，不受影响。

**代价与代价控制**：只读包装只动了返回值类型/同一性（`retriever.collection is X`
不再成立），**读语义未动**（自愈回落、句柄缓存、注入 fake 集合的既有用法都在）；
共 1 处既有断言涉及同一性，即本批自己的回归用例第 13 条，已随包装调整为「读走注入
的 fake」，并新增 4 条 r2 用例。

**取证（同一 legacy 直写读属性，改前 vs 改后，均在生产库副本上）**

| 代码 | 调用 | 结果 | 权威集合（副本） |
|---|---|---|---|
| 改前（55ea3f7 原样） | `retriever.collection.upsert(...)` | `wrote`（静默） | **27,115 → 27,116** `23be3a808bc7 → c6beb5943781` **[已变化]** |
| 改后（r2） | 同一写法 | **`raised ReadPropertyWriteRefused`** | 27,115 → **27,115** **[零变化]** |
| 改后（r2） | `add_chunks(...)`（正常写路径） | `wrote` | 27,115 → **27,115** **[零变化]**，落到显式集合 |

> 残留（已披露、不可由库层消除）：两步取别名（`c = retriever.collection; c.upsert(...)`）
> 不会被**静态规则**命中 —— 但会被**只读包装在运行时**拦下；两层叠加即为本批的闭环。

> **边界（诚实披露）**：r2 的库层拦截覆盖**公开读属性** `collection` 经写方法名的
> 写入；`retriever._collection.upsert(...)` 或 `retriever.collection._wrapped.upsert(...)`
> 这类**私有属性逃生口**仍可绕过 —— 私有访问不在契约内（k26 那类「注入 fake 集合」
> 的既有测试用法正依赖 `_collection` 是裸句柄，故不封）。静态守卫同样只覆盖
> `\.collection\.<写方法>` 形态；两层叠加后的闭环是「公开路径全封 + 私有路径需
> 显式越权」，若控制方要求连私有路径也封，请指示（会与既有注入用法冲突）。

### R2-3 生产指纹口径（对齐用，一行命令）

```bash
python3 scripts/k59/prod_fingerprint.py --json     # 只读，不构造 chroma 客户端
```

口径（逐字节确定，NULL 记空串）：
- `count` = 集合 METADATA segment 下去重 `embedding_id` 数；
- `meta_md5` = 全量 `(embedding_id, key, string_value, int_value, float_value, bool_value)`
  按 `(embedding_id, key)` **排序** → 每行 **TAB** 连接、行间 `\n`、UTF-8 → MD5；
- `ids_md5` = 排序后 `embedding_id` 以 `\n` 连接 → MD5；
- `emb_rows` = `embeddings` 表中属于该集合的行数；
- `vec_*` = VECTOR segment（HNSW 目录）文件大小 + `header.bin` /
  `index_metadata.pickle` 的 MD5。

当前生产基线（本批 r1/r2 前后逐字节一致）：
`count=27115`、`meta_rows=108460`、`meta_md5=23be3a808bc7d1b3f6d1a2a1152a5cf6`、
`ids_md5=9ea7d255044f12c1a4ee6704efdd7d8f`、`emb_rows=27115`、
`vec_header_md5=04ffc7b77dee64839c19ca14054214af`、
`vec_index_metadata_md5=cc1000e074ad4caf01df1a040f4c1c11`。
试过但**都不等于** brief 的 `abc00d5b43863c2fe67e65556361ee74` 的 8 种口径：
TSV/JSON × DB 自然序/排序 × 带 `id` 列/不带 × 仅 id / 仅文档 / repr / SQL 字面量。
若控制方能给出该值的生成方式，后续批次可切到该口径对齐。

### r2 测试数字

```bash
TMPDIR=/dev/shm K59_PROD_REPLICA=/dev/shm/k59/replica_pytest nice -n 10 \
  python3 -m pytest tests/test_k59_retriever_write_guard.py tests/test_k59_collection_write_scan.py -q
# → 28 passed（写路径护栏 17 条：13 条 r1 + 4 条 r2，含生产副本门控用例；静态守卫 11 条）

TMPDIR=/dev/shm nice -n 10 python3 -m pytest tests/test_rag.py \
  tests/test_k24_ref_content_crash.py tests/test_k26_sentinel_title.py \
  tests/test_k28_minors.py tests/test_k30_solar_projection.py \
  tests/test_k31_self_heal_counter.py tests/test_k38_e6_red_fixes.py \
  tests/test_k59_retriever_write_guard.py tests/test_k59_collection_write_scan.py -q
# → 230 passed, 1 skipped
```

生产指纹在 r2 实验后仍与基线**逐字节一致**（`before == r2` → True）。全量 pytest
仍未跑（按纪律）。r2 实验同样只在 `/dev/shm` 副本上（新增 2 份副本，共 6 份，
复核完可 `rm -rf /dev/shm/k59 /dev/shm/k59_old`）。
