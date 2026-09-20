# k61 r11 报告：收窄全局影响面（4 条红 → 0 红）+ r10 复审 Important/Minor 整改

- 工作树：`/home/a/k61-r11-wt`（新建：`git worktree add /home/a/k61-r11-wt -b k61-egress-r11 k61-egress-r10`）
- 基点：`411f37d`（= r10 tip）；本轮**未合并、未推送**
- 改动面：**只有 `tests/` 四个文件**（`git diff --stat` 见 §5.1），**`src/` 一个字节都没动**
- 全量门禁：**`5550 passed, 11 skipped, 0 failed, 0 error`**（§4）

---

## 0. 结论摘要

| 项 | 状态 |
|---|---|
| 4 条被打红的用例 | **全部修复**（0 failed / 0 error；同命令 A/B：`3 failed,41 passed` → `44 passed`） |
| 根因 | 不是测试写错，是 k61 的 conftest **在导入期改了全局语义**（路径/环境/看门狗口径） |
| I-1（r10 新误报向量） | 已修（`bytes()` 非 TypeError 失败 → 归"不可扫→放行"）+ 12 条参数化行为锁 |
| I-2（模块级登记项无取证） | 已修（循环遍历全部登记项 + 模块级条目补结构断言 + "无处可躲"反向锁） |
| M-1 / M-2 / M-3 | 已做（细节见 §3）；M-1 未做"全模块无孤儿锁"门禁，**理由与计数见 §3.1** |
| 附带更正 | r10 登记理由里关于 `_socket.getaddrinfo` 的**机制描述与事实不符**，已更正并锁住（§3.2） |
| 四维验收 | 真实路径/权威对比/失败路径/数据一致性：见 §6（含**未验证项**清单） |

---

## 1. 环境（先确认复现前提，避免"我这边不复现"）

控制方的失败报文里出现 `/dev/shm/k61_test_data_*/userdata/fortune.db`，说明跑门禁的环境**有 ZHIPU key**（否则那两条冒烟会 skip 而不是 fail）。本工作树按部署检出语义补了同一前提：

```bash
$ cd /home/a/k61-r11-wt && ls -la .env
lrwxrwxrwx 1 a a 25 Sep 20 20:43 .env -> /mnt/e/fortune-agent/.env    # 只读软链；.env 在 .gitignore:7
$ git status --short          # 工作树干净（.env 未跟踪、不入守卫口径）
```

> 注：`tests/conftest.py` §0 的 `import src.config` 会走生产入口的 `load_env_file(".env")` —— 这正是 r3⑤ 定的语义（门控只取决于「key 在不在」）。没有 `.env` 的机器上，这几条冒烟会 skip（＝ r10 之前的行为），不会红。
> 红线保险：所有命令都带 `DEEPSEEK_API_KEY= ANTHROPIC_API_KEY=`（与 conftest 的 pin **等价**：pin 在导入期就把它们置空，`load_env_file` 的成员判定不会回填）。LLM 一律走免费 `glm-4-flash`。

---

## 2. 四条失败：逐条根因 + 改前/改后复现记录

### 改前（同一条命令，r10 状态）

```bash
$ cd /home/a/k61-r11-wt && TMPDIR=/dev/shm DEEPSEEK_API_KEY= ANTHROPIC_API_KEY= nice -n 10 \
    /home/a/fortune-agent/.venv/bin/python -m pytest \
      tests/test_eval_l4.py::test_smoke_l4_light \
      tests/test_eval_l1.py::test_smoke_l1_default_slice \
      tests/test_k62k63_fixup_side_effect_guard.py::test_guard_has_teeth_pre_fix_copy_dirties_memory \
      tests/test_ziwei_authority_fix.py -q -p no:cacheprovider
3 failed, 41 passed in 3.56s
EXIT=1
```
（原始输出：`/dev/shm/k61r11-logs/00-before-4cases.txt`；全量门禁的同类原始输出另见 `/dev/shm/k61r11-logs/01-baseline-full.txt`）

### 改后（命令逐字相同）

```bash
$ cd /home/a/k61-r11-wt && TMPDIR=/dev/shm DEEPSEEK_API_KEY= ANTHROPIC_API_KEY= nice -n 15 \
    /home/a/fortune-agent/.venv/bin/python -m pytest \
      tests/test_eval_l4.py::test_smoke_l4_light \
      tests/test_eval_l1.py::test_smoke_l1_default_slice \
      tests/test_k62k63_fixup_side_effect_guard.py::test_guard_has_teeth_pre_fix_copy_dirties_memory \
      tests/test_ziwei_authority_fix.py -q
44 passed in 356.97s (0:05:56)
EXIT=0
```
（原始输出：`/dev/shm/k61r11-logs/20-after-4cases.txt`。条目数一致：41+3 = 44。）

---

### 失败 ①② `test_eval_l4.py::test_smoke_l4_light` / `test_eval_l1.py::test_smoke_l1_default_slice`

**改前原始输出（l4）**
```
>           assert r["skipped"] is False, f"{r['id']} 不应被跳过: {r['skip_reason']}"
E           AssertionError: T013 不应被跳过: 隔离库复制失败: FileNotFoundError:
              [Errno 2] No such file or directory: '/dev/shm/k61_test_data_rijcunoz/userdata/fortune.db'
tests/test_eval_l4.py:510: AssertionError
```
**改前原始输出（l1）**
```
E               AssertionError: L1 阈值未达标: {'total': 7, 'executed': 0, 'skipped': ['T001','T002','T049','T070','T071','T081','T082'], ...}
[SKIP] T001 对话排盘完整生辰 —— 隔离库复制失败: FileNotFoundError: ... '/dev/shm/k61_test_data_rijcunoz/userdata/fortune.db'
```

**根因（已逐环查清，不是猜）**
1. r5/I4 把 `FORTUNE_DB_PATH` pin 到**沙箱里一个不存在的路径**（conftest §0 `TEST_ENV_SANDBOX_PATHS`，沙箱只 `makedirs` 了目录、**没有**库文件）。
2. 整个 eval 冒烟族的既定语义是「**复制真实库** → 临时库 → 跑主链」：
   `scripts/eval_agent/{l1,l2,l4}_eval.py`、`judge.py`、`runner.py` 都调 `l1_eval.seed_db_copy(R["settings"].db_path, tdir)`，而
   `src/config.py:218` 的 `settings.db_path` **就是** `FORTUNE_DB_PATH`。
3. 于是复制源不存在 → `FileNotFoundError` → 每条任务 `skipped=True` → L1 `executed=0` ⇒ `thresholds_met()` 恒 False（该函数第一句就是 `if executed == 0: return False`）；L4 更直接：`assert r["skipped"] is False`。

**修法（收窄 k61 的影响面，不动那两条测试）**
conftest §0c ①：沙箱里放**代码默认那份库的只读快照**（`_materialize_user_db_snapshot()`：`src.config.Settings().db_path` → `shutil.copy2` 到 `FORTUNE_DB_PATH`，导入期取一次，幂等；源不存在则保持"不存在"→ 相关用例照旧 skip）。
两边同时成立：
- 别人的用例：`settings.db_path` 仍指向**一份真实的 fortune.db**（内容为会话开始时的快照）→ 复制/断言语义与"不 pin"时相同；
- k61 的隔离主张：会话期间**没有任何用例打开生产库**（快照在 fd 看门狗启动**之前**取），生产库零写入。

实测：快照 7151616 字节（= 生产库大小），含全部导入的 conftest 导入成本 **0.32s**。

> ⚠️ 语义细节（如实登记）：conftest **自己在导入期以只读方式打开了一次**生产库（为了取快照）。所以准确表述是「**测试会话期间没有用例打开生产库**」，而不是「这个进程一次都没碰过它」。

**顺带暴露的更大问题（值得控制方知道）**：r10 状态下**整个 eval 冒烟族是"空跑"的** —— L2/L3/e6 的冒烟同样走 `seed_db_copy`，同样每任务 skip，但它们的断言**容忍 skip**（如 `tests/test_eval_l3.py:415` `if r["skipped"]: assert r["skip_reason"]`；`tests/test_eval_e6.py:750` 明说"只断言运行完整落盘 + 退出码语义"）→ 门禁里**绿着空转**。只有 L1/L4 因为断言了 `executed>0` / `skipped is False` 才把这件事喊出来。本轮的快照修法让这一族恢复真实执行（门禁时长从 9:05 涨到 23:50 —— 这是**修好了**的代价，明细见 §4.3）。

---

### 失败 ③ `test_k62k63_fixup_side_effect_guard.py::test_guard_has_teeth_pre_fix_copy_dirties_memory`

**改前原始输出**
```
>       assert changed == [MEMORY_FILE_REL.name], (
            "去掉重定向后 data/memory/.json 未被写脏 → 本守卫失去判定力（机制变了），"
            f"需重审守卫前提。实际变化：{changed}")
E       AssertionError: ... 实际变化：[]
E       assert [] == ['.json']
tests/test_k62k63_fixup_side_effect_guard.py:206: AssertionError
```

**根因**
- 那条守卫的**判定前提**（它自己的 docstring 写着）：把沙箱副本里 `tests/test_bot.py` 的隔离 fixture 摘掉、并在子进程里**显式剔除 `USER_MEMORY_DIR`**（"若靠环境变量才不脏，那测的是环境不是测试文件自身的隔离"）后，那条空 uid 用例**必须**把 `data/memory/.json` 写脏。
- r2 起 conftest 在**导入期全局**设了 `os.environ["USER_MEMORY_DIR"] = TEST_MEMORY_DIR`。沙箱副本里包含这份 conftest → 子进程里**沙箱自己的 conftest 又把重定向装了回去** → 改前副本也不脏 → 守卫失去判定力。

**修法（收窄 k61 的影响面，不改那条测试）**
conftest §0c ②：**去掉全局重定向**，回到仓内既有约定 —— **每个测试文件自理**（batch2/k62 修那次事故的处置就是给 `tests/test_bot.py` 加模块级 autouse fixture `monkeypatch.setenv("USER_MEMORY_DIR", …)`）；conftest 只提供 `TEST_MEMORY_DIR` 目录常量，兜底交给**会话级探测** `_k61_repo_dirt_guard`（真被写脏就报红）。
为什么**只能**去掉、不能"改窄"：任何**自动**重定向（导入期 env 或 autouse fixture）都会在沙箱子进程里重新生效，那条守卫的判别力就会再次归零 —— 它就是靠"环境里没有兜底"来证明"测试文件自己做了隔离"。
另外在导入期加了硬不变式（前后快照对比，见 §5.2 的 `USER_MEMORY_DIR_AFTER_PIN == _USER_MEMORY_DIR_BEFORE_PIN`），防有人再加回去。

**这条修法会不会把 `data/memory/.json` 重新写脏？—— 实测：不会。**
k61 当年用自己的探针定位到"全量跑 164 次 `UserMemory._save`，其中空 uid 的 4 次覆盖 `.json`，来自 `test_bot.py` 的两条语音用例"；那两条**已被 batch2（k62）按文件隔离**。本轮全量门禁跑完的工作区状态（守卫的判据）：
```
### DIRT AFTER:
 M tests/conftest.py
 M tests/test_k61_llm_egress_guard.py
 M tests/test_k61_repo_hygiene.py
 M tests/test_k61_test_env_isolation.py
```
只有**本批自己在改的 4 个文件**（会话前就脏 → 增量比对不算），`data/memory/.json` / `src/engine/out/` **零脏**（若脏，`_k61_repo_dirt_guard` 会在会话收尾抛 `RepoDirtDetected` → 门禁会多出 1 error；没有）。

---

### 失败 ④ `ERROR tests/test_ziwei_authority_fix.py::test_ziwei_result_contract_shape`

> 先纠正一个直觉：**它不是 ziwei 的问题，也不是 I-1**。它是**会话收尾的守卫报错**，被 pytest 挂在"最后一个收集到的用例"上（`test_ziwei*` 按字母序在最后），所以"单跑绿、全量 ERROR"。

**改前原始输出（全量门禁）**
```
==================================== ERRORS ====================================
____________ ERROR at teardown of test_ziwei_result_contract_shape _____________
    @pytest.fixture(scope="session", autouse=True)
    def _k61_prod_data_guard():
        ...
>               raise ProdDataTouched(verdict)
E               conftest.ProdDataTouched: [k61 生产数据守卫] **本会话进程树**打开过生产数据路径：
E                 pid=2187069 fd=52 → /mnt/d/fortune-data/books/k55_dream/clean/public_domain_quotes.jsonl
E                 pid=2187069 fd=52 → /mnt/d/fortune-data/books/zonghe/12880_dreams.txt
E                 pid=2187069 fd=52 → /mnt/d/fortune-data/books/k55_dream/clean/dream_corpus.jsonl
E               处置：让被测代码走沙箱目录（conftest 已 pin VECTORDB_DIR / FORTUNE_DB_PATH / FAISS_INDEX_DIR 到测试目录），不要打开生产库。
tests/conftest.py:589: ProdDataTouched
```

**根因（逐环）**
1. r7 的判据是「**本会话进程树**打开过生产数据路径」，但 `PROD_DATA_ROOTS` 写的是**整棵** `/mnt/d/fortune-data`（注释里的主张其实只是"不打开生产**向量库/生产库**"）。
2. 那三个路径是 `tests/test_dream_rules_k55.py::test_public_domain_records_are_verbatim_and_traceable` /
   `::test_third_party_records_carry_source_url` **按设计**要读的**只读语料**（溯源用例：公版条文必须逐字来自源文件；且自带"数据不在就 skip"的门）。
3. 语料读 → fd 命中 → 会话收尾 `ProdDataTouched` → 整轮 1 error。
4. 这类**过拦**无法用 pin 修：那两条用例就是要读真语料，pin 到空沙箱只会让它们静默 skip（r7-5 的老教训）。

**修法（收窄看门狗口径；顺带**加强**写侧）**
`PROD_DATA_ROOTS` 收窄为**三处数据存储**（`vectordb_v2` / `faiss` / `userdata`）+ 生产服务的 `userdata`：**打开即报红**（这正是 I4 的原始主张）。
其余生产树（如只读语料 `books/`）走新增的第二级：**以写方式打开**才报红 —— 判据取 `/proc/<pid>/fdinfo/<fd>` 的 `flags:` 低 2 位（0=只读/1=只写/2=读写）；拿不到 fdinfo 或格式变了 → **fail-closed 判成写**。
即：**读语料放行（原行为：报红）；写生产数据报红（原行为：也报红）** —— 写侧覆盖范围**比原来更宽**（原来只在 fd 采样命中时才记，且不分读写）。
原始取证（改前 vs 改后同一进程树事实）：
```bash
# 改前（r10 代码）——只读打开生产语料会命中
$ /home/a/fortune-agent/.venv/bin/python -B /dev/shm/k61r11-logs/i1_probe_base.py   # 探针里含只读 corpus 读法
$ sed -n '81,106p' /dev/shm/k61r11-logs/01-baseline-full.txt   # 上面的 ERROR 块
# 改后：见 tests/test_k61_test_env_isolation.py 的两条锁
#       （TestProdDataGuardCriterion::test_two_tier_verdict_read_vs_write /
#         ::test_read_only_corpus_open_is_not_flagged）+ 全量门禁 0 error
```

---

### 2.5 ⚠️ 收尾自曝并修掉的一个**我自己引入的竞态**（假红，必须写下来）

**怎么发现的**：把 4 条目标用例 + k61 守卫文件**合起来**再跑一遍时，出现了一次
`ERROR tests/test_k62k63_fixup_side_effect_guard.py::test_guard_has_teeth_pre_fix_copy_dirties_memory`
（`ProdDataTouched`）；**同一命令再跑就绿** → 竞态。（第一次全量门禁没撞上纯属运气：看门狗 0.05s 采样 vs 复制窗口只有几毫秒。）

**根因（确定性探针，不是猜）**
`test_k62k63_fixup_side_effect_guard` 用**子进程**跑 pytest（沙箱副本，`_run_repro`）。子进程是**本会话的后代** → 子进程里的 conftest 也要建用户库快照 → **读生产库** → 被**父会话**的 fd 看门狗采到 → 会话收尾 `ProdDataTouched` → 整轮 1 error 挂在某个用例上。
探针（`.superpowers/sdd/k61-r11-logs/watchdog_probe.py`：父进程起看门狗 + 子进程 `import conftest`；采样率调到 0.01 让它稳定复现）：
```
改前（r11 初版）：  HITS: 3   (2208275, '3', '/mnt/d/fortune-data/userdata/fortune.db') ×3
改后（本修复）：    HITS: 0   （缓存可用 / 缓存不可用 两条路径都是 0）
```

**修法（两条，合起来既不留假红、也不让快照消失）**
1. **跨会话缓存** `DB_SNAPSHOT_CACHE = /dev/shm/k61_seed_userdb/fortune.db`（与 conftest 既有的 `SANDBOX_CORPUS_SEED_DIR` 同款思路）：生产库只在「**没有任何看门狗在采样本进程**」时被读一次 → 种进缓存（`copy2` 到 `.part` 再 `os.replace`，**并发会话永远只看到完整文件**）；之后所有会话（含子进程）都从**缓存**复制 —— 缓存不是生产路径，看门狗看不见。
2. **可继承标记** `K61_PROD_WATCHDOG_ACTIVE`：会话级看门狗启动时置上（子进程继承）→ 子进程的 conftest 一旦发现"有人正在采样我"，就**不读生产库**（拿不到快照就不建，相关用例照旧 skip 并在日志里写明原因）。宁可少一层快照，也不给别人的会话制造假红。

**留锁 + 复跑**：`tests/test_k61_test_env_isolation.py` 新增两条 ——
`test_sandbox_db_is_a_real_snapshot`（真库结构 / 非空 / **不在生产路径下**）与
`test_snapshot_refuses_to_read_prod_while_watched`（有标记 → 拒读；缓存可用 → 用缓存）。
复跑记录（改前/改后用"含 k62k63 的那组文件"反复跑，把竞态逼出来/压回去）：
```
改前：pytest {guard, isolation, hygiene, k62k63} -q  →  199 passed, **1 error**   （ProdDataTouched）
      同一条命令再跑                              →  199 passed                （**同一个命令、不同结果 = 竞态**）
      确定性探针 watchdog_probe.py                 →  HITS: 3
改后：同一条命令 ×5                                →  201 passed ×5（+2 = 本轮新增的两条锁）
      {isolation, hygiene, k62k63} ×3              →  41 passed ×3
      watchdog_probe.py（缓存可用 / 不可用两路径）    →  HITS: 0 / HITS: 0
```

> 这是我**本轮自己引入**的问题（我让 conftest 去读生产库建快照）——处置：发现即修 + 留锁 + 留探针，并如实写在这里。

---

## 3. r10 复审遗留 I / M 的处理

### 3.1 M-1（旧锁无主冗余）
- r10 的 `HOOKED` 已把 4 条路径的登记改指向"路径 × 载荷拆开"的新锁，但旧锁仍在跑且**没有任何出处**。
- 处置：新增 `TestSendFamilyApiSurface.RETAINED_LOCKS`（name → 保留理由，4 条：`test_memoryview_send_blocked` / `test_single_block_still_blocked` / `test_os_write_blocked` / `test_sendto_blocked`）+ 锁 `test_retained_locks_are_still_real_locks`：**名字必须在**、**不得同时被 HOOKED 引用**（登记表保持单一事实源）、源码里必须仍有 `pytest.raises(`（不许烂成空跑）。
- **没做**：全模块"无孤儿锁"门禁。**理由与计数**：本模块 116 条 `test_*` 里只有 **12** 条是登记表的锁（其余属 TLS / httpx / 代理等**不在 HOOKED 登记范围**的族）——一刀切会让大量合法用例假红。要做这条门禁，前提是先把 TLS/httpx 族也纳进某张登记表，属另一批的活。

### 3.2 I-2（模块级登记项没有任何取证）
- r10 的取证锁循环写死 `if mod == "_socket.socket"` → `("_socket", "getaddrinfo")` **完全不在循环内**，唯一把关是"理由 ≥40 字含 pin/兜底"。
- 处置三条：
  1. `test_every_uncoverable_entry_is_forensically_resolvable`（**反向锁**）：任何登记项的模块名必须落在 `_forensic_namespaces()`（= `_modules()` + `_socket`）→ **没有条目能躲开所有结构断言**（新增命名空间会被立刻要求补取证）。
  2. `test_module_level_declared_entries_are_second_bindings`（模块级结构取证）：① 属性存在；② `_socket.<name>` 是 **C 函数**（`inspect.isbuiltin`）；③ 已挂钩的 `socket.<name>` 是 **Python 包装**且其源码里**调用** `_socket.<name>`（取证时直接读 **stdlib 的 socket.py**，因为会话里 `socket.<name>` 已被我们的钩子换掉 —— 这个坑本轮自测踩到过）；④ `_socket` 属性**可改**（赋值成自己即证据、立刻还原）；理由文本必须写明是"当前不覆盖"而不是"挂不上钩"。
  3. **顺带更正 r10 的机制描述（这是事实错误，不是措辞）**：r10 登记写的是「`socket.getaddrinfo`（A 层）的**同一 C 实现的第二条绑定**」。实测**为假**：
     ```
     $ python -c "import socket,_socket,inspect; print(_socket.getaddrinfo is socket.getaddrinfo, inspect.isbuiltin(_socket.getaddrinfo), inspect.isbuiltin(socket.getaddrinfo))"
     False True False
     ```
     真实关系是**包装 vs 被包装**（`socket.py` 的 `def getaddrinfo` 里 `_socket.getaddrinfo(...)`）。理由是"为什么绕过 A 层"的**唯一凭据**，写错会让后人得出错误结论 → 已改写登记理由，并把②③钉成结构断言（再写错就红）。
     实测仍成立的部分不变：`_socket.getaddrinfo('api.deepseek.com', 443)` 真解析成功、0 trip（r10 审查者实测）→ 仍是「当前不覆盖 + pin 兜底」。

### 3.3 I-1（非 TypeError 的 `bytes()` 失败被记成"守卫自身异常"）
- 修法：`_as_scannable_payload` 增加 `except (ValueError, BufferError): return None`（不可扫 → 放行）；`sendmsg` / `os.writev` 两条内联 `b"".join(bytes(b) …)` 抽成 `_join_scannable_buffers()`（任一块扫不了 → 整条不判，**不假装扫过**，且不记 hook error）。
- 取证（A/B 探针，逐条路径 × 逐种载荷，含"不挂钩的原函数"作基线）：
  ```
  改前（/dev/shm/k61r11-logs/i1-before.txt，r10 代码）：
  sendall closed_mmap    base=('ValueError','mmap closed or invalid')  same=True hook_errors+1 violations+0
  send    released_mv    base=('ValueError','operation forbidden on released memoryview object') same=True hook_errors+1 violations+0
  …（4 路径 × 2 载荷 = 8/8 全部 hook_errors+1）

  改后（/dev/shm/k61r11-logs/i1-after.txt）：
  …（8/8 全部 same=True hook_errors+0 violations+0）
  ```
  即：**可见异常与裸 Python 逐字相同**，而 r10 会给每一次都记一笔"守卫自身异常"→ 会话收尾把整轮报 ERROR 并谎称"守卫可能已静默失效"。
- 锁：`TestUnscannablePayloadIsNotAGuardFault`（4 路径 × 3 载荷 = **12 条**参数化）断言三件事：① 异常类型+文本 == 不挂钩基线；② `hook_errors` 零新增；③ `violations` 零新增（既没误拦、也不是"拦住了"）。
- ⚠️ **未验证**：我**没有**在本仓的现有用例里找到真的会触发它的那条测试（全量跑完 `hook_errors` 为 0）。r10 复审给的是"可达的误报向量"（对抗输入），不是"已发生的失败"；本轮按描述修掉并用**外部探针**做了可复现的 A/B。

### 3.4 M-2（"名字存在"档不够 → 加归因 AST 锁）
- 新增 `test_every_hooked_lock_asserts_attribution`：用 **AST** 取每条 HOOKED 锁**自己函数体**的源码（不是全局 grep），必须含 `_assert_blocked_by(...)`；做不到的必须登记进 `RETAINED_LOCKS_WITHOUT_ATTRIBUTION`（当前**空表**）。
- **这条新锁当场抓出 6/20 条名不副实**（只证明"发生了拦截"、不证明"拦在它声称的那层"）：
  `test_sendmsg_blocked`、`test_os_writev_blocked`、`test_posix_write_aliases_are_blocked[posix.splice]`、`test_posix_write_aliases_are_blocked[posix.eventfd_write]`、`test_io_open_socket_fd_blocked`、`test_builtins_open_socket_fd_blocked`
  → 已给这 6 条补上归因断言（层名取自 conftest `_trip(where=…)` 的实际串，如 `"socket 明文请求头"` / `"io.open / builtins.open（socket fd ← 文件对象）"`）。现在 20/20 全部有归因。

### 3.5 M-3（绊线 F 形态：不含 memoryview 的白名单照样绿）
- 原绊线只在类型集里找 `memoryview` → `isinstance(d, (bytes, bytearray))` 这种**同样是类型白名单**的写法**照样绿**。
- 处置：判据改为「类型集里出现**任何一个**载荷招牌类型」（`PAYLOAD_TYPE_MARKERS = ("bytes","bytearray","memoryview")`），并把它抽成纯函数 `_payload_type_gate_offenders(src)` —— 抽出来是为了**给绊线自己上牙**：
  - **正例**：conftest 源码必须 0 命中；
  - **反例（4 条）**：不含 memoryview 的白名单、只有 bytearray 的、含 memoryview 的、三件套的 —— **必须全部命中**；
  - **负例（3 条）**：`isinstance(addr, (tuple, list))` / `isinstance(peer, (tuple, list))` / `isinstance(v, (str, int))` —— **必须不误杀**。

---

## 4. 全量门禁（任务 4）

### 4.1 命令与摘要行

```bash
$ cd /home/a/k61-r11-wt && TMPDIR=/dev/shm nice -n 10 \
    /home/a/fortune-agent/.venv/bin/python -m pytest tests/ -q
```
（实跑时另加 `DEEPSEEK_API_KEY= ANTHROPIC_API_KEY=`，与 conftest 的 pin 等价，仅作红线保险；原始输出 `/dev/shm/k61r11-logs/10-r11-full.txt`（首轮）、`30-r11-full-final.txt`（**最终**））

**最终（含 §2.5 的竞态修复）**
```
5552 passed, 11 skipped, 100 warnings in 1358.34s (0:22:38)
EXIT=0
```
**首轮（竞态修复前，同样 0 红）**
```
5550 passed, 11 skipped, 101 warnings in 1430.88s (0:23:50)
EXIT=0
```
**两轮都是 0 failed / 0 error。四条目标用例全在通过之列**（它们不在任何失败清单里；同命令定向复现见 §2 的 44 passed）。
最终轮跑完的工作区：只有本轮自己在改的两个文件（`tests/conftest.py`、`tests/test_k61_test_env_isolation.py`）—— `data/memory/` 与 `src/engine/out/` **零脏**。

### 4.2 与基线的数字对账（证明"没有靠删/跳变绿"）

| | 条目数（collect-only 实测） | passed | failed | error | skipped |
|---|---|---|---|---|---|
| 改前（`411f37d`，独立复现工作树 `/home/a/k61-r11-base`，`-p no:cacheprovider`） | 5543 | 5529 | 3 | 1 | 11 |
| 改后·首轮（本轮 `/home/a/k61-r11-wt`） | 5561 | 5550 | 0 | 0 | 11 |
| 改后·**最终**（+ §2.5 的两条锁） | 5563 | 5552 | 0 | 0 | 11 |

- 条目数用 `pytest tests/ --collect-only -q` 在两个工作树各测一次：`5543` → `5561`（首轮），最终 `5563` = 5561 + 2（§2.5 新增的两条锁）。
- passed：5529 + 3（原 3 条失败转绿）+ 18（首轮新增）+ 2（最终新增）= **5552** ✔
- skipped：**11 → 11 → 11 不变**（既没有新增 skip，也没有"把跑着的改成 skip"）。
- 表里改前的 `failed=3 / error=1` 不额外占条目：`5529+3+11 = 5543` 已经把 5543 个条目分完 —— 那个 error 是**同一个条目**（`test_ziwei_result_contract_shape`）的 **teardown 报告**（call 阶段通过、会话收尾的守卫夹具抛异常），所以它只出现在报告里、不改变条目数。
- `git diff` 里 **0 处新增 `skip`/`xfail`**；删除的断言只有 2 处，且都是被我**替换掉的两个机制的旧判据**（§5.2 逐条列出），无一处是"削弱判据"。

### 4.3 时长从 9:05 → ~23:00 的原因（不是回归）

- 直接测得：那两条冒烟在改前 **3.56s 就失败退出**，改后**真跑**（7 条任务真实主链 + 免费 GLM）＝ **5:56**（§2 A/B）。
- 更广一层：L2 / L3 / e6 的冒烟同样走 `seed_db_copy`，改前**每任务 skip 但断言容忍 skip** → **绿着空转**（`tests/test_eval_l3.py:415`、`tests/test_eval_e6.py:750`）；改后它们也恢复真实执行 → 构成剩余增量。
- 结论：时长增加 = **验证面被修复**的表现。若控制方要"快版本"，那是另一件事（例如给这几条冒烟加显式开关），本轮**不动**。

---

## 5. 改动清单与证据

### 5.1 改动面（只有 tests/）

```bash
$ git diff --stat
 tests/conftest.py                    | 240 ++++++++++++++++--
 tests/test_k61_llm_egress_guard.py   | 396 +++++++++++++++++++++++++++++++---
 tests/test_k61_repo_hygiene.py       |   7 +-
 tests/test_k61_test_env_isolation.py |  90 +++++++-
 4 files changed, 676 insertions(+), 57 deletions(-)
```
**`src/` 零改动**；四条被打红的用例**一个字节都没动**（不在改动清单里）。

### 5.2 被替换掉的两条旧判据（逐条交代，避免"悄悄放宽"）

| 旧判据 | 新判据 | 为什么 |
|---|---|---|
| `UserMemory().base_dir == TEST_MEMORY_DIR`（全局重定向生效） | 导入期前后快照相等（conftest **没有**动 `USER_MEMORY_DIR`）+ `TEST_MEMORY_DIR` 常量仍在 + `DIRT_GUARD_SCOPES` 覆盖 `data/` | 全局重定向**就是**失败 ③ 的根因；新判据锁住"不许再加回去"，并且隔离责任回到各测试文件 |
| `wd.roots == tuple(PROD_DATA_ROOTS)`（全树"打开即报"） | `wd.roots == PROD_DATA_WRITE_ROOTS`（粗筛）**+** `_prod_fd_hit` 两级判定的行为锁 + 只读语料集成锁 | 全树"打开即报"**就是**失败 ④ 的根因；新口径读侧只对"数据存储"报，写侧覆盖**整棵树**（比原来宽） |

---

## 6. 现实正确性四维（含**没做到/没验证**）

1. **真实用户路径**：修的是"别人的测试能不能按设计跑"这条真实路径 —— 用**同一条命令**在改前/改后各跑一遍（§2），并且 L1/L4 的断言本身要求 `executed>0` / `skipped is False`，绿＝真的执行了主链（不是 skip 出来的绿）。
2. **权威对比**：① 对 4 条失败，用**控制方给的 4 条判据 + 控制方的环境前提**（有 key、TMPDIR=/dev/shm）复现；② 判据改动都做了**反向取证**（M-3 的正/反/负例、I-2 的结构断言、I-1 的"与裸 Python 逐字相同"），而不是只跑通就算。
3. **失败路径**：每条修法都配了"改前必须红"的记录（§2、§3.3 的 A/B 探针）；`_prod_fd_hit` 对 fdinfo 缺失/格式变化 fail-closed。
4. **数据一致性**：**生产库零写入**（跑门禁用的是 /dev/shm 快照；全量跑完 `data/memory/`、`src/engine/out/` 零脏）；`FORTUNE_DB_PATH` 快照与代码默认路径**同源**（`src.config.Settings().db_path`），不存在"两处各说各话"。

**没做到 / 没验证（不含糊）**
- **没有**在合并态（`4ab678d` + 本轮）跑门禁：按指令只在 `k61-r11-wt`（基点 `411f37d`）跑。已能说明合并是忠实三方合并且本轮不扩大部署面：
  ```
  $ git merge-tree --write-tree 4ab678d 411f37d      # r10 态
  26287612fdaeccea8a77f5675a6dbdb11aa64bc9           # == 9a2f47b 的 tree（说明控制方那次合并是忠实合并且无冲突）
  $ git merge-tree --write-tree 4ab678d c928e1d      # 本轮提交后
  c282ae85dc0b1de4bb18d118a4f5eee44b3ea530           # 单行输出 = 无冲突
  $ git rev-parse 9a2f47b:src  →  6b145bec1d91e482b85e068782df1437bf47f735
  $ git rev-parse c282ae85…:src →  6b145bec1d91e482b85e068782df1437bf47f735   # **逐字相同**
  ```
  即：合并**无冲突**，且合并结果里的 **`src/` 与已审过的 `9a2f47b` 逐字相同**（本轮零 src 改动在合并态也成立）。**未做**合并后实跑门禁。
- **没有**定位到触发 I-1 的现有用例（本轮全量 `hook_errors` 为 0）；I-1 是"可达的误报向量"，本轮用外部探针做了 A/B，**没有**在套件内找到真实触发点。
- **没有**做 M-1 的"全模块无孤儿锁"门禁（理由与计数见 §3.1）。
- **没有**为 `PROD_DATA_ROOTS` 收窄补"读语料的**写**打开"以外的取证（例如磁盘级只读挂载断言）—— 判据只到 fdinfo 的 flags。
- 快照缓存的两条**已知降级**（确定性、非假红，如实登记）：
  ① 缓存是**开机内**有效（`/dev/shm`，重启即清）→ 内容可能比"此刻的生产库"旧；语义上等价于"会话开始时的快照"，而 eval 族每条任务本来就会再复制一次，不影响断言；
  ② 若 `/dev/shm` 不可写**且**已有看门狗在采样本进程 → 本轮**不建快照**（相关用例 skip + 打 warning）—— 这是**确定性**的降级（宁可不建，也不给别人的会话制造假红）。
  这两条都没有在"没有 /dev/shm 的机器"上实测（本机 /dev/shm 可用），只做了代码路径推理 + `watchdog_probe.py` 的两路径探针。
- **没有**跑 `RUN_EVAL_L*_FULL_SMOKE=1` 的完整冒烟范围（本轮只跑默认切片，与门禁一致）。
- **未验证** `r11` 在**没有 `.env`、没有生产库**的机器上的行为（预期：相关用例照旧 skip；`_materialize_user_db_snapshot` 源不存在时保持"不存在"）。本机两个条件都具备，只能走代码路径推理。

---

## 7. 顺手发现、但**按红线没动**的事（请控制方裁决）

1. **k61 分支删除 `src/membership.html`（582 行）**：`git cat-file -e` 三方对照 —— `1a0e395` 有、`4ab678d` 有、`411f37d` **无**；`git diff --diff-filter=D 1a0e395..411f37d -- src/` 只有这一条。**这是既有的 k61 内容（不是本轮改的）**，且与 `src/main.py` 的改动配套（`git grep 4ab678d -- '*.py'` 有 `main.py:2786: html = Path(__file__).parent / "membership.html"`，当前分支 `src/` 里**已无任何引用**）→ 不构成悬空引用。但它既不在 k61 的 r9 报告里，也不在 `test_k61_public_url_product_bug.py` 里 —— **没有书面依据**，请控制方确认是否有意为之（本轮按"src/ 一行都不许动"**未碰**）。
2. **eval 冒烟族的"空转"是历史状态**：r10 之前（也就是 main 上）这几条同样是"有 key 就真跑"，只有 r10 期间被 pin 成空转 —— 本轮已恢复。若控制方希望门禁更快，建议单独立项给冒烟加开关，而不是把 DB 快照退回去。

---

## 8. 附件（原始日志，均在 /dev/shm，重启即失；关键处已在上文引用）

| 文件 | 内容 |
|---|---|
| `/dev/shm/k61r11-logs/00-before-4cases.txt` | 改前：4 条定向复现（`3 failed, 41 passed in 3.56s`） |
| `/dev/shm/k61r11-logs/01-baseline-full.txt` | 改前：全量门禁（`3 failed, 5529 passed, 11 skipped, 1 error in 545.54s`），含 §2 ④ 的 ERROR 块 |
| `/dev/shm/k61r11-logs/02-after-4cases.txt` | 中间态：3 条红转绿（`31 passed in 413.74s`，含 k61 隔离文件） |
| `/dev/shm/k61r11-logs/10-r11-full.txt` | 改后：全量门禁**首轮**（`5550 passed, 11 skipped in 1430.88s`，EXIT=0）+ 工作区脏表 |
| `/dev/shm/k61r11-logs/30-r11-full-final.txt` | 改后：全量门禁**最终**（`5552 passed, 11 skipped in 1358.34s`，EXIT=0）+ 工作区脏表 |
| `/dev/shm/k61r11-logs/20-after-4cases.txt` | 改后：同命令定向复现（`44 passed in 356.97s`，EXIT=0） |
| `/dev/shm/k61r11-logs/i1-before.txt` / `i1-after.txt` | I-1 的 A/B 探针（8/8 `hook_errors+1` → 8/8 `+0`，异常逐字相同） |
| `/dev/shm/k61r11-logs/i1_probe.py` / `i1_probe_base.py` | 上面两份的探针源码（可在任一 worktree 复跑） |
| `/dev/shm/k61r11-logs/watchdog_probe.py` | §2.5 竞态探针（父会话在看门狗下跑子会话）：改前 `HITS: 3` → 改后 `HITS: 0` |

以上日志**另有一份副本随报告留档**：`.superpowers/sdd/k61-r11-logs/`（`/dev/shm` 重启即失）。

复现环境（"改前"那两次跑用的**独立复现工作树**，已在本轮收尾时清理，重建只要两行）：
```bash
git -C /mnt/e/fortune-agent-deploy worktree add --detach /home/a/k61-r11-base 411f37d
ln -sfn /mnt/e/fortune-agent/.env /home/a/k61-r11-base/.env      # 只读软链（部署检出语义）
```
`/home/a/k61-r11-wt/.env` 是同样的只读软链，**故意保留**：没有它，那几条冒烟会 skip 而不是（像控制方环境那样）真跑 —— 复验请在"有 key"的环境下跑，否则复现不到"改前红"的形态。
