# -*- coding: utf-8 -*-
"""k61：测试进程四道防线（环境隔离 / LLM 出站守卫 / 免费 GLM 路由 / 仓库卫生）。

## 为什么需要它（根因）

`src/config.py` 在**模块导入时**执行 `load_env_file(".env")`，把 cwd（或仓库根）
的 `.env` 灌进 `os.environ`（不覆盖已存在变量）。部署/生产检出的 `.env` 是
指向生产 `.env` 的软链，内含 `DEEPSEEK_API_KEY` / `ZHIPU_API_KEY` / `EXPERIENCE_MODE=true`。
于是**任何 `import src.*` 的测试进程都会拿到生产 DeepSeek key**；若干用例的
门控写法是「拿不到 key 就 pytest.skip」→ 全量跑时不再 skip，直接对
`api.deepseek.com` 发起**真实付费**调用。

实测证据（k61 探针，见 `.superpowers/sdd/task-k61-report.md`）：`.env` 在场时
4 个测试文件里有 7 条用例共发出 27 次 `api.deepseek.com` 出站请求；
k57 门禁里 `test_response_under_5_seconds` 真跑到 >5s 墙钟失败即其表现。
守卫上线后又揪出 4 个文件之外 10 条同类静默外呼（裸 `Mock()` 的 `.api_key`
自动生成真值 → `getattr(...,'')` 判成「已配置」→ 真发请求 → 失败被吞）。

## 本文件提供四道防线

0. **测试进程环境隔离**（r2 / 方案 A）：`TEST_ENV_PINS` 在任何 `src.*` 导入之前
   把部署键 pin 成空值 —— 测试进程等价于「没有部署 .env」。见 §0。
   **r11 收窄**（本文件曾被全量门禁打红，根因是"改了别人依赖的全局语义"）：路径类
   pin 的沙箱里放**真实库的只读快照**（`FORTUNE_DB_PATH`，不能是空目录 —— 否则
   eval 冒烟的"复制真实库"当场 FileNotFoundError）；`USER_MEMORY_DIR` **不再全局
   重定向**（会拆掉 `test_k62k63_fixup_side_effect_guard` 的判定力），改回"每个测试
   文件自理 + conftest 只提供目录常量"。详见 §0c。
1. `_k61_deepseek_egress_guard`（autouse / session）——**进程级**拦截：
   测试期间任何指向 `deepseek` 域的真实出站连接一律以
   `DeepSeekEgressBlocked(BaseException)` 失败。拦截点全部落在「真正会把字节
   送出去、或真正会建立客户端 TLS 的那一层」，**逐条清单（含"没覆盖的"）
   以 `tests/test_k61_llm_egress_guard.py` 的枚举为唯一事实源**：
   `HOOKED`（已挂钩 + 有行为锁）/ `DECLARED_UNCOVERABLE`（做不到）/
   `DECLARED_NOT_A_CHANNEL`（名字像但不是通道，带实测）/ `DECLARED_LIMITATIONS`
   （**降级：钩上了但判据不完整，可被绕过**）。摘要：
     - A 层 `socket.getaddrinfo`（覆盖 httpx/anyio/requests/urllib/原生 socket）
     - B 层 `socket.create_connection`（标准库同步路径）
     - C 层 `socket.socket.connect`/`connect_ex`（按**解析后的 IP** 判）
     - D 层 `send`/`sendall`/`sendmsg`/`sendto` + `os.write`/`writev`/`sendfile`/`splice`/
       `eventfd_write` + `posix.*` 同族（**跨块累积**判明文请求头）
     - E 层 `httpx.HTTPTransport.handle_request` / `AsyncHTTPTransport.handle_async_request`
       （**只有真实传输层**才命中；`httpx.MockTransport` 是独立类，不受影响 →
       用 mock transport 固定请求形状的用例照常工作）
     - F 层 客户端 TLS 的**全部入口**（`SSLContext.wrap_socket`/`wrap_bio`、
       `SSLSocket._create`、`SSLObject._create`、C 层 `_wrap_socket`/`_wrap_bio` 的
       遮蔽层）：判据是**合取** —— SNI 在白名单**且**真实对端可接受（见
       `_tls_target_refused`；r8 曾只看 SNI ⇒ SNI 可撒谎，r9 修回）。
     - G 层 把 socket fd 包成**文件对象**的族（`io.FileIO` / `os.fdopen` /
       `io.open` / `builtins.open` / `shutil.copyfileobj`）：C 层直写 fd，内容看不见 → 拒。
   免费源白名单 `FREE_LLM_HOSTS`（只放免费源，当前 `open.bigmodel.cn`）不受影响；
   该白名单在 `tests/test_k61_llm_egress_guard.py` 里做正向对照（白名单必须放行）。

   为什么用 `BaseException` 子类：被测代码普遍写 `except Exception:` 做兜底降级
   （advisor/mood_detector/jian_quote 都是），用 `AssertionError` 会被**静默吞掉**，
   守卫就形同虚设。另配 `_VIOLATIONS` 记录 + session 收尾断言，双保险。

   ⚠️ 有了 §0 的 pin 之后，**这一层是兜底而不是唯一防线**：pin 让测试进程连
   DeepSeek key 都没有（结构性免疫），守卫负责拦住那些不依赖环境变量、
   用 Mock/硬编码 key 直接调统一层的调用点。

   ⚠️ **本层不是"全覆盖"**（k61 r8 曾把"结构锁 + 几条行为锁"写成"每条路径都有
   行为锁"，被独立审查实测证伪：`grep wrap_bio tests/` 0 命中、把整条守卫摘掉
   守卫测试文件仍 91 passed）。r9 的处置：**每条声称覆盖的路径都补行为锁，并把
   "摘掉该路径的守卫 → 对应锁变红"实测记录写进
   `.superpowers/sdd/task-k61-r9-report.md`**；做不到的一律不进 HOOKED，
   改列 DECLARED_*（连同"可被怎么绕"的实测）。

2. `glm_route` fixture——需要**真实 LLM** 的端到端用例走**免费 `glm-4-flash`**。
   做法与仓库既有评测路由完全同款（`scripts/verify_qa_scenarios.py`、
   `scripts/eval_agent/l1_eval.py` 的 `_routed`）：把统一 LLM 层的
   `src.llm.client.deepseek_anthropic_completion` 接到
   `src.llm.client.glm_openai_completion`（model 固定 `glm-4-flash`）。
   无 `ZHIPU_API_KEY` 时 skip（与 `tests/test_eval_l4.py::_llm_keys_ready` 同约定）。

3. `_k61_repo_dirt_guard`（autouse / session）——仓库卫生：会话期间**新弄脏**
   被 git 跟踪的文件即在会话收尾报 `RepoDirtDetected`（同样 `BaseException`）。
   见 §0b。
"""
import atexit
import builtins
import io
import logging
import os
import posix
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import weakref

import pytest


# ══════════════════════════════════════════════════════════════════
# 0) 测试进程环境隔离（r2 / 方案 A：测试侧 pin，**零生产改动**）
# ══════════════════════════════════════════════════════════════════
#
# 问题：`src/config.py` 导入即 `load_env_file(".env")`；部署/生产检出的 `.env`
# （软链到生产）会把**生产值**灌进测试进程的 `os.environ`。除已收口的
# `DEEPSEEK_API_KEY`（见 k61 P1）之外，还有一批会**改变测试行为**的值：
#   - `EXPERIENCE_MODE=true` → 「付费内容全免费 + 跳配额」分支
#     （`is_experience_mode()`，消费点 src/main.py、api/union.py、services/chat_quota.py）
#     —— 仓内测试早已**各自打补丁绕开**它：`test_chat_quota.py` /
#     `test_member_pay.py` 都有「.env 是体验版，测试默认关闭」的 autouse fixture，
#     `test_qian_kinds.py` 直接 `os.environ["EXPERIENCE_MODE"] = ""`。
#     这正说明它是**会改断言的泄漏**，只是靠散落的补丁掩盖着 —— 新增用例不会知道要补。
#   - `DEV_OPENID` / `DEV_TOKEN_ENDPOINT` → dev 身份与 dev 造令牌端点开关
#   - 生产密钥（JWT_SECRET_KEY / ENCRYPTION_KEY / ADMIN_KEY / …）→ 测试不得继承
#
# 修法（协调方批准的方案 A）：在**任何 `src.*` 导入之前**把这些键 pin 成空值。
# `load_env_file` 的语义是 `if key not in os.environ` → 已存在即**不覆盖**，
# 因此 pin 生效且**不需要动 `src/config.py`**（生产启动路径零影响）。
# ZHIPU_API_KEY 与 PUBLIC_BASE_URL **故意不 pin**：前者是用户指定的免费测试
# LLM（pin 掉会让所有 GLM 端到端用例 skip），后者按协调方指示仅登记、待独立裁决。

#: 部署 `.env` 里**不得进测试进程**的键 → pin 成 `""`（= 未配置 / 代码默认）。
#: 键必须带理由（见 tests/test_k61_test_env_isolation.py 的卫生用例）。
TEST_ENV_PINS = {
    "EXPERIENCE_MODE": "内容/配额总开关：true 会把「付费内容全免费 + 跳配额」带进测试，"
                       "使配额/付费墙断言静默走错分支（仓内已有 3 处散落补丁在绕它）",
    "DEV_OPENID": "把开发态 openid 钉死成部署值（src/api/user.py 的 dev 身份）",
    "DEV_TOKEN_ENDPOINT": "dev 造令牌端点的开关；生产若被误改成 1，测试进程会带上该后门",
    "LOG_LEVEL": "生产日志级别；pin 空 = 代码默认 INFO（src/logging_config.py 的兜底语义）",
    "JWT_SECRET_KEY": "生产签名密钥：测试必须自备，不得继承（否则用生产密钥签发/验签）",
    "ENCRYPTION_KEY": "生产加密密钥：测试必须自备，不得继承（否则用生产密钥加解密用户数据）",
    "ADMIN_KEY": "生产超管密钥：测试不得继承（否则测试进程持有超管令牌 = 越权面）",
    "FORTUNE_API_KEY": "生产接口鉴权密钥：测试不得继承（否则测试进程持有对外接口凭据）",
    "WECHAT_APP_ID": "生产微信应用凭据：测试不得继承（微信侧调用会打到生产账号）",
    "WECHAT_APP_SECRET": "生产微信应用密钥：测试不得继承（同上）",
    "WECHAT_SUBSCRIBE_TEMPLATE_ID": "生产订阅消息模板：测试不得继承（会真发订阅消息）",
    "MIDAS_APP_KEY": "生产米大师密钥：测试不得继承（涉及真实支付通道）",
    "MIDAS_OFFER_ID": "生产米大师商品号：测试不得继承（涉及真实支付商品）",
    "ANTHROPIC_API_KEY": "k61 r5（I3）：`resolve_llm_api_key()` 的**回退面** —— DEEPSEEK 为空时"
                         "会回退到它。不 pin 掉的话「测试进程不持有生产 LLM key」只是**名义上**成立"
                         "（带该变量的机器上，守卫的 pin 层被绕过），而且那条锁会**假红**",
    "DEEPSEEK_API_KEY": "付费生产模型密钥：测试一律不得持有（k61 红线）。"
                        "pin 掉后「测试进程拿到生产 DeepSeek key」在**结构上不可能**，"
                        "守卫（DeepSeekEgressBlocked）退为兜底；"
                        "需要该键的用例自行 monkeypatch.setenv（如 test_k33_llm_unified_route）",
}

#: **故意不 pin** 的键（pin 掉会破坏测试能力）。由锁用例做正向对照。
TEST_ENV_NOT_PINNED = {
    "ZHIPU_API_KEY": "用户指定的免费测试 LLM（glm-4-flash）——pin 掉会让所有 GLM "
                     "端到端用例 skip，等于关掉本批的验证面",
    "PUBLIC_BASE_URL": "协调方指示：仅登记、由独立审查者核查其真实影响后裁决，"
                       "本批**不动**（pin 它会掩盖生产值，妨碍那次核查）",
}

#: k61 r5（I4）：**路径类**配置必须 pin 到**沙箱目录**，而不是 pin 成空值 ——
#: 因为这三个键的**代码默认值就是生产路径**（`/mnt/d/fortune-data/...`），
#: pin 成空值等于没 pin（r3 的 `FAISS_INDEX_DIR` pin 就是这种"空操作"）。
#: 实测后果：`test_engine_run_comparison` → `evidence.py:41` / `baseline.py:47`
#: 会**真的打开生产向量库**并 bump 其 SQLite 的 mtime（审查者 11:12:37 实测）。
TEST_DATA_DIR = tempfile.mkdtemp(prefix="k61_test_data_")
TEST_VECTORDB_DIR = os.path.join(TEST_DATA_DIR, "vectordb_v2")
TEST_FAISS_DIR = os.path.join(TEST_DATA_DIR, "faiss")
TEST_DB_DIR = os.path.join(TEST_DATA_DIR, "userdata")
for _d in (TEST_VECTORDB_DIR, TEST_FAISS_DIR, TEST_DB_DIR):
    os.makedirs(_d, exist_ok=True)
atexit.register(shutil.rmtree, TEST_DATA_DIR, ignore_errors=True)

#: 路径类键 → 沙箱目录（**不是**空串；见上注）。
TEST_ENV_SANDBOX_PATHS = {
    "VECTORDB_DIR": ("向量库目录。代码默认 = 生产目录 → 原样跑会让 evidence/baseline "
                     "真的打开生产向量库（只读，但会 bump chroma.sqlite3 的 mtime）"),
    "FAISS_INDEX_DIR": "FAISS 索引目录。代码默认 = 生产目录（原 pin 成空值是**空操作**）",
    "FORTUNE_DB_PATH": "用户数据库路径。代码默认 = 生产目录",
}

#: **生产数据存储**（数据库/索引/用户库）—— 本会话**打开过**即报红。
#: 这正是 I4 的原始主张：「跑测试不打开**生产向量库/生产库**」。
#:
#: ⚠️ **r11 收窄（这是合并后 1 error 的根因）**：r7–r10 这里写的是**整棵**
#: `/mnt/d/fortune-data` —— 把 `books/` 下的**只读语料**也算成了"生产库"。
#: 而 `tests/test_dream_rules_k55.py::test_public_domain_records_are_verbatim_and_traceable`
#: /`::test_third_party_records_carry_source_url` 是**按设计**要读真实语料的溯源用例
#: （`books/k55_dream/clean/dream_corpus.jsonl`、`books/zonghe/12880_dreams.txt`，
#: 且自带"数据不在就 skip"的门）—— 全量门禁跑到会话收尾，看门狗把这几次**只读**
#: 打开记成"打开过生产数据" → `ProdDataTouched` → 整轮报 ERROR（挂在最后一个用例上，
#: 所以表现为 `ERROR tests/test_ziwei_authority_fix.py::…`，单跑却绿）。
#: 语料是**只读数据源**、不是库；把"读语料"判成违规属于**过拦**（且无法通过 pin 修：
#: 那两条用例就是要读真语料，pin 空沙箱只会让它们静默 skip —— 与 r7-5 同款教训）。
#: 现在：**数据存储 = 打开即报红**；**整棵生产树 = 只对"以写方式打开"报红**。
PROD_DATA_ROOTS = (
    "/mnt/d/fortune-data/vectordb_v2",   # 向量库（k61 已 pin VECTORDB_DIR）
    "/mnt/d/fortune-data/faiss",         # FAISS 索引（已 pin FAISS_INDEX_DIR）
    "/mnt/d/fortune-data/userdata",      # 用户业务库（已 pin FORTUNE_DB_PATH）
    "/home/a/data/userdata",             # 正在运行的生产服务自己的用户库
)

#: 生产数据**整棵树**：只读不算违规（见上注），**以写方式打开**即报红。
PROD_DATA_WRITE_ROOTS = ("/mnt/d/fortune-data", "/home/a/data")

#: 会话级临时目录：**供测试文件自行**把 `UserMemory()` 的默认目录（repo 内
#: `data/memory/`）重定向出去。这是 `UserMemory` 自己文档化的隔离口
#: （`USER_MEMORY_DIR`，「测试隔离用」），`tests/test_bot.py` 就是这么用的
#: （模块级 autouse fixture `monkeypatch.setenv`）。
#:
#: ⚠️ **r11：conftest 不再替所有测试设这个环境变量**（原来在 `_pin_test_env()` 里
#: 全局设过）。原因见下面 §0c 的长注 —— 全局重定向会让
#: `tests/test_k62k63_fixup_side_effect_guard.py::test_guard_has_teeth_pre_fix_copy_dirties_memory`
#: 失去判定力（那条守卫的判定前提正是「**没有**环境变量兜底时，未隔离的用例会把
#: `data/memory/.json` 写脏」）。本常量保留，供需要的测试文件显式使用。
TEST_MEMORY_DIR = tempfile.mkdtemp(prefix="k61_test_memory_")
atexit.register(shutil.rmtree, TEST_MEMORY_DIR, ignore_errors=True)


def _pin_test_env() -> None:
    """在导入任何 `src.*` 之前 pin 环境（本文件模块级调用，见下方调用点）。

    conftest 在任何测试模块被 import 之前加载，而 `src.config` 是在测试模块
    顶层（或函数内）被 import 的 —— 所以**模块级**执行才能在 `load_env_file`
    之前把键占住。放进 fixture 就太晚了（fixture 在收集和 import 之后才跑）。
    """
    for key in TEST_ENV_PINS:
        os.environ[key] = ""
    # I4：路径类 pin 到沙箱（不能 pin 空值 —— 空值会落到代码默认 = 生产路径）
    os.environ["VECTORDB_DIR"] = TEST_VECTORDB_DIR
    os.environ["FAISS_INDEX_DIR"] = TEST_FAISS_DIR
    os.environ["FORTUNE_DB_PATH"] = os.path.join(TEST_DB_DIR, "fortune.db")


#: r11：`USER_MEMORY_DIR` 的导入期**前后快照**（锁用例据此断言「本 pin 函数没有
#: 动过它」）。用前后对比而不是「此刻是否为空」：开发者 shell 里若自己导出了这个
#: 变量，断言"必须为空"会假红，而 pin 的职责只是"不替所有测试做决定"。
_USER_MEMORY_DIR_BEFORE_PIN = os.environ.get("USER_MEMORY_DIR")

_pin_test_env()

#: 见 §0c：conftest 不得在导入期占用 `USER_MEMORY_DIR`（全局重定向会拆掉别人的判定力）。
USER_MEMORY_DIR_AFTER_PIN = os.environ.get("USER_MEMORY_DIR")

# ── r3 ⑤：把 `.env` 的加载**定死**（消灭"取决于还导入了谁"的顺序依赖）──
# 此前 `.env` 是否进进程，取决于**这一次收集了哪些文件**（哪个模块恰好先
# `import src.config`）。后果：审查者实测单独跑 `pytest tests/test_adaptive_advisor.py`
# 时，即使部署 `.env` 里 ZHIPU key 在场，4 条 GLM 门控用例也**静默 skip**；
# 两个文件一起跑才真跑 —— 「真跑还是 skip」竟取决于命令行给了哪些文件，不可接受。
# 修法：conftest（必然被加载）显式走**生产入口的加载路径**（`uvicorn src.main:app`
# 也必然 import src.config），于是任何测试会话都确定性地加载 `.env`；
# 泄漏值由上面的 pin 压住，ZHIPU（免费测试 LLM）保持可用 —— 门控只取决于
# 「key 在不在」，不再取决于「还导入了谁」。
import src.config  # noqa: E402,F401  （导入即 load_env_file(".env")；必须在 pin 之后）

#: **导入期快照**：在任何测试模块被 import 之前拍下的 pin 生效状态。
#: 锁用例据此断言「pin 确实生效」—— 这是**顺序无关**的判据：
#: 收集期就有约 20 个测试模块会直接 `os.environ["JWT_SECRET_KEY"] = "test-..."`（仓内
#: 既有约定、不回滚），所以「此刻 os.environ 是否为空」不能作为 pin 的判据；
#: 而 pin 的**唯一职责**就是在 `load_env_file(".env")` 之前把键占住 —— 那件事
#: 只发生在导入期，导入期快照正好只看那件事。
#: 若有人删掉上面的 `_pin_test_env()` 调用，本快照会拍到 `.env` 的生产值
#: （没有 `.env` 时拍到 None）→ 锁用例立刻报红。
ENV_PINS_AT_IMPORT = {key: os.environ.get(key) for key in TEST_ENV_PINS}


# ══════════════════════════════════════════════════════════════════
# 0c) r11：收窄全局影响面 —— 两处「改了别人依赖的语义」的整改
# ══════════════════════════════════════════════════════════════════
#
# k61 合并进 main 后跑全量门禁打红了**别人的**用例（控制方 A/B 实测：同一批 4 条
# 用例在 `4ab678d`（无 k61）为 `2 passed, 2 skipped`，加上 k61 后 `3 failed, 1 passed`）。
# 逐条查清后，根因是本文件**在导入期改了全局语义**，而别人的用例依赖那些语义。
#
# 四处整改（前两处在下面，后两处见各自的长注）：
#   ① `FORTUNE_DB_PATH` 沙箱里放**真实的库快照**（不是空目录）—— 见下；
#   ② `USER_MEMORY_DIR` **不再全局重定向**（改回每个测试文件自理）—— 见下；
#   ③ 生产数据看门狗从"整棵 `/mnt/d/fortune-data` **打开即报**"改成**两级判定**
#      （数据存储=打开即报；整棵生产树=**写**才报）—— 见 `PROD_DATA_ROOTS` 的长注；
#   ④ 守卫的 `bytes()` 归一化**不再把"不可扫"记成"守卫自身异常"**（r10 复审 I-1）——
#      见 `_as_scannable_payload` / `_join_scannable_buffers`（钩子层）。

# ── ① `FORTUNE_DB_PATH`：沙箱里必须是**一份真实的库**（只读快照），不能是空目录 ──
#
# r10 把 `FORTUNE_DB_PATH` pin 到沙箱里一个**不存在的**路径。后果（实测复现）：
# `tests/test_eval_l1.py::test_smoke_l1_default_slice` /
# `tests/test_eval_l4.py::test_smoke_l4_light` 的既定语义是「把 `settings.db_path`
# 那份库复制到临时库再跑主链」（`scripts/eval_agent/l1_eval.py::seed_db_copy`：
# `shutil.copy2(R["settings"].db_path, tdir)`），pin 之后源文件不存在 →
# `FileNotFoundError: .../userdata/fortune.db` → L1 七条任务、L4 两条任务**全 skip**
# → `executed=0` → 阈值断言必红（L4 那条更直接：`assert r["skipped"] is False`）。
#
# 修法：沙箱里放**代码默认那份库的只读快照**（会话导入期取一次）。两边同时成立：
#   - 别人的用例：`settings.db_path` 仍指向**一份真实的 fortune.db**（内容 = 会话
#     开始时的快照），复制/断言语义与「不 pin」时相同；
#   - k61 的隔离主张：整个**测试会话期间**没有任何用例打开生产库（快照在导入期取，
#     早于 fd 看门狗启动），生产库仍是**零写入**。
# 快照源 = `src/config.py` 的**代码默认值**（`Settings().db_path`，不经 env）——
# 也就是「不 pin 时那个键会取到的值」，语义逐字对齐。生产库不存在的机器上保持
# 「不存在」→ 相关用例照旧 skip（不制造假数据）。
#
# ⚠️ **r11 收尾补的一个竞态（实测到的假红，必须记下来）**：快照不能在**任何**会话里
# 都去读生产库 —— `tests/test_k62k63_fixup_side_effect_guard.py` 会用**子进程**跑
# pytest（沙箱副本），那些子进程里 conftest 也要建快照，而**子进程是本会话的后代** →
# 「子进程读生产库」被**父会话**的 fd 看门狗采到 → 会话收尾 `ProdDataTouched` → 整轮
# ERROR 挂在某个用例上。这是**竞态**（看门狗 0.05s 采样）：全量门禁那次侥幸没撞上，
# 37s 的定向集撞上了。确定性复现探针（`HITS: 3`，fd=3 → `/mnt/d/fortune-data/userdata/fortune.db`）：
#     $ TMPDIR=/dev/shm python -B .superpowers/sdd/k61-r11-logs/watchdog_probe.py /home/a/k61-r11-wt
# 修法两条（合起来既不留假红、也不让快照消失）：
#   ① **跨会话缓存**（与 §0 的沙箱语料缓存同款思路）：生产库只在"**没有任何看门狗
#      在采样本进程**"时被读一次 → 种进 `/dev/shm` 缓存（原子落盘，防并发读到半个文件）；
#      之后所有会话（含子进程）都从缓存复制 —— 缓存不是生产路径，看门狗看不见。
#   ② **继承标记** `PROD_WATCHDOG_ACTIVE_ENV`：会话级看门狗启动时置上（子进程继承）
#      → 子进程的 conftest 一旦发现"有人正在采样我"，就**不读生产库**（拿不到快照就
#      不建，相关用例照旧 skip 并写明原因）—— 宁可少一层快照，也不给别人制造假红。
DB_SNAPSHOT_CACHE = "/dev/shm/k61_seed_userdb/fortune.db"

#: "已有看门狗在采样本进程（或其祖先）"的**可继承标记**（见上注 ②）。
PROD_WATCHDOG_ACTIVE_ENV = "K61_PROD_WATCHDOG_ACTIVE"


def _snapshot_source():
    """快照的**源文件**：缓存优先；返回 `None` = 本轮不建快照（相关用例照旧 skip）。"""
    cache = DB_SNAPSHOT_CACHE
    if os.path.exists(cache):
        return cache
    try:
        prod = str(src.config.Settings().db_path)      # 纯代码默认（不经 env）
    except Exception as exc:                           # pragma: no cover
        logging.getLogger(__name__).warning("[k61] 取用户库代码默认值失败：%s", exc)
        return None
    if not os.path.exists(prod):
        return None                    # 没数据的机器：保持"不存在"（用例照旧 skip）
    if os.environ.get(PROD_WATCHDOG_ACTIVE_ENV):
        logging.getLogger(__name__).warning(
            "[k61] 快照缓存不可用，且**已有看门狗在采样本进程**（%s）→ 本轮不建用户库"
            "快照（读生产库会被记成「本会话打开过生产数据」）：依赖真实库的用例会 skip",
            PROD_WATCHDOG_ACTIVE_ENV)
        return None
    # 没人看我 → 读一次生产库、种缓存（**原子落盘**：并发会话永远只看到完整文件）
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        tmp = f"{cache}.{os.getpid()}.part"
        shutil.copy2(prod, tmp)
        os.replace(tmp, cache)
        return cache
    except Exception as exc:                           # pragma: no cover
        logging.getLogger(__name__).warning(
            "[k61] 用户库快照缓存种植失败（%s）；本进程未被看门狗采样，直接读生产库", exc)
        return prod


def _materialize_user_db_snapshot() -> None:
    """把用户库的**只读快照**放进 `FORTUNE_DB_PATH` 沙箱（r11 ①；幂等）。"""
    dst = os.environ["FORTUNE_DB_PATH"]
    if os.path.exists(dst):
        return
    source = _snapshot_source()
    if source is None:
        return                         # 拿不到源也不越线：相关用例照旧 skip
    try:
        shutil.copy2(source, dst)      # 只读复制：生产库内容/mtime 零改动
    except Exception as exc:                            # pragma: no cover
        # 复制失败不算会话失败：相关用例会照旧 skip 并写明原因（同 r7-5 语料种植的处置）
        logging.getLogger(__name__).warning(
            "[k61] 用户库快照失败（依赖真实库的用例会 skip）：%s", exc)


_materialize_user_db_snapshot()


# ── ② `USER_MEMORY_DIR`：conftest **不再**全局重定向（改回"每个测试文件自理"）──
#
# r10 在 `_pin_test_env()` 里全局设了 `USER_MEMORY_DIR`。它确实压住了
# `data/memory/.json` 的脏（k61 探针：全量跑 164 次 `UserMemory._save`，其中空 uid
# 的 4 次覆盖了那个**被跟踪**文件），但代价是**改掉了所有测试依赖的目录语义**：
#
# `tests/test_k62k63_fixup_side_effect_guard.py::test_guard_has_teeth_pre_fix_copy_dirties_memory`
# 的判定前提是「把 `tests/test_bot.py` 的隔离 fixture 摘掉后，跑那条空 uid 用例
# **必须**把 `data/memory/.json` 写脏」—— 它为此在子进程里**显式剔除**
# `USER_MEMORY_DIR`（原话：若靠环境变量才不脏，那测的是环境不是测试文件自身的隔离）。
# conftest 全局设上之后，那个子进程里**沙箱自己的 conftest** 又把重定向装了回来 →
# 改前副本也不再脏 → 守卫失去判定力（实测报文 `实际变化：[]`，k61 合并后 3 failed 之一）。
#
# 为什么**只能**去掉而不能"改窄"：任何**自动**重定向（导入期 env / autouse fixture）
# 都会在沙箱子进程里重新生效，那条守卫的判别力就会再次归零 —— 它是靠"环境里没有
# 兜底"来证明"测试文件自己做了隔离"的。而"per-file 自理"正是仓内**既有约定**：
# batch2（k62）修那次事故的处置就是给 `tests/test_bot.py` 加模块级 autouse fixture
# （`monkeypatch.setenv("USER_MEMORY_DIR", …)`），本文件只提供 `TEST_MEMORY_DIR` 目录。
# 兜底由 `_k61_repo_dirt_guard`（会话级**探测**：真被写脏就报红）承担 —— 分工是
# 「谁写的谁负责隔离，conftest 只负责发现」。
# 取证：r11 报告「全量门禁」一节 —— 去掉全局重定向后全量跑 `data/memory/.json`
# 仍与 HEAD 逐字节相同（两个元凶用例已由 batch2 各自隔离）。
assert USER_MEMORY_DIR_AFTER_PIN == _USER_MEMORY_DIR_BEFORE_PIN, (
    "conftest 在导入期改了 USER_MEMORY_DIR —— 会拆掉 "
    "test_k62k63_fixup_side_effect_guard 那条守卫的判定力（见 §0c ②）")


# ══════════════════════════════════════════════════════════════════
# 0b) 仓库卫生守卫（r2 / ④）：测试不得写脏**被 git 跟踪**的文件
# ══════════════════════════════════════════════════════════════════
#
# 实测（r2 基线，全量 `pytest tests/ -q`）：跑完必脏两个被跟踪文件 ——
#   M data/memory/.json                    （走 UserMemory 默认目录的用例）
#   M src/engine/out/comparison_runs.jsonl （run_comparison 的硬编码输出路径）
# 两者都已在 r2 修根因（见 §0 的 USER_MEMORY_DIR 隔离 —— r11 起由各测试文件自理 + §0c ① 的
# run_comparison `out_path` 参数）。本守卫是**防复发锁**：会话结束时比对「会话开始时干净、
# 结束时变脏」的**被跟踪**文件，有则报红。
#
# 为什么要做**增量**比对而不是直接断言「工作区干净」：开发期间工作区本来就
# 可能有未提交改动（本批自己在改 14 个文件），一刀切会让守卫在正常开发时假红。

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


#: r3 ⑦：本守卫**只负责这些产物范围**的脏（审查者 03:35 实测到假红 ——
#: 多个会话在同一个 worktree 里并发开发时，**任何**源文件都可能被合法改动，
#: 而那不是「测试写脏了仓库」。把范围收到"运行时产物"上，守卫才只对
#: 「测试往仓库里写」这件事报警，不会对别人的正常编辑假红）。
DIRT_GUARD_SCOPES = {
    "data/": "运行时产物（用户记忆 data/memory/*.json 等）—— 默认落到仓库内的写点",
    "src/engine/out/": "对比跑批归档（run_comparison 的默认输出目录）",
}


def _tracked_dirty() -> set:
    """当前处于「已修改/已删除/已暂存」状态的**被跟踪**文件集合（忽略未跟踪）。"""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=30,
        )
    except Exception:  # pragma: no cover - git 不可用（如源码压缩包）时守卫自动失效
        return set()
    if out.returncode != 0:  # pragma: no cover
        return set()
    dirty = set()
    for line in out.stdout.splitlines():
        if len(line) > 3:
            dirty.add(line[3:].strip().strip('"'))
    return dirty


def _scoped_dirty() -> set:
    """只保留 `DIRT_GUARD_SCOPES` 范围内的脏 —— 并发会话改源码不算（见上注）。"""
    return {p for p in _tracked_dirty()
            if any(p == s.rstrip("/") or p.startswith(s) for s in DIRT_GUARD_SCOPES)}


class RepoDirtDetected(BaseException):
    """测试会话把**被 git 跟踪**的文件写脏了（r2 ④）。

    同样继承 `BaseException`：不得被被测代码的 `except Exception` 吞掉。
    """


#: 会话起点的工作区状态（conftest **导入期**快照 —— 比收集期更早，连
#: 「测试模块在 import 时写脏文件」也能覆盖）。守卫据此做增量比对；
#: 锁用例 `tests/test_k61_repo_hygiene.py` 也读它。
SESSION_START_DIRTY = _scoped_dirty()


#: k61 r5（I4）：**生产数据文件**（代码默认路径）—— 会话期间 mtime 不得变化。
#: 只看代码默认的那套生产路径（`/mnt/d/fortune-data`）；**不看** `/home/a/data`
#: （那是**正在运行的生产服务**自己的库，生产流量随时会写它 → 看了会假红）。
#: 目的：结构性地证明"跑测试不打开生产向量库/生产库"（审查者实测
#: `test_engine_run_comparison` → evidence.py 曾打开 `/mnt/d/fortune-data/vectordb_v2`
#: 并 bump chroma.sqlite3 的 mtime）。
def _guard_sqlite_connect(orig):
    """r7：`sqlite3.connect(path)` 的路径钩子 —— 直接看**连的哪个库**（兜住极短打开）。"""
    def inner(database, *a, **kw):
        try:
            text = str(database)
            if any(text.startswith(root) for root in PROD_DATA_ROOTS):
                _PROD_WATCHDOG.hits.append((os.getpid(), "sqlite3.connect", text))
        except Exception:
            pass
        return orig(database, *a, **kw)
    return inner


PROD_DATA_FILES = (
    "/mnt/d/fortune-data/vectordb_v2/chroma.sqlite3",
    "/mnt/d/fortune-data/userdata/fortune.db",
    "/mnt/d/fortune-data/faiss/docs.db",
)


def _prod_data_mtimes() -> dict:
    out = {}
    for path in PROD_DATA_FILES:
        try:
            out[path] = os.stat(path).st_mtime_ns
        except OSError:
            out[path] = None
    return out


# ── r11：两级判定（纯函数，供行为锁直接调；见 PROD_DATA_ROOTS 的长注）──

def _is_write_open(fdinfo_text) -> bool:
    """`/proc/<pid>/fdinfo/<fd>` 的内容是否表示**以写方式**打开（O_WRONLY / O_RDWR）。

    `flags:` 是八进制；低 2 位即访问模式（0=只读、1=只写、2=读写）。
    **解析失败/拿不到 → 判成"写"**（fail-closed：宁可报红也不漏 —— 与守卫其余部分一致）。
    """
    for line in str(fdinfo_text or "").splitlines():
        if line.startswith("flags:"):
            try:
                return (int(line.split(":", 1)[1].strip(), 8) & 0o3) in (0o1, 0o2)
            except ValueError:      # pragma: no cover - 格式变了
                return True
    return True                     # pragma: no cover - 读不到 fdinfo


def _read_fdinfo(pid, fd) -> str:
    """读 fdinfo（只读；失败返回 ""，由 `_is_write_open` 兜成 fail-closed）。"""
    try:
        with open(f"/proc/{pid}/fdinfo/{fd}", "r", encoding="utf-8") as fh:
            return fh.read(4096)
    except OSError:
        return ""


def _prod_fd_hit(path, fdinfo_text=None) -> bool:
    """单个 fd 是否算命中生产数据守卫（**纯判定**，两级）。

    - 路径在 `PROD_DATA_ROOTS`（数据存储）→ **打开即命中**（只读也算：这正是 I4 主张）；
    - 路径只在 `PROD_DATA_WRITE_ROOTS`（如只读语料 books/）→ 仅**写方式打开**命中。
    """
    text = str(path)
    if any(text.startswith(root) for root in PROD_DATA_ROOTS):
        return True
    if any(text.startswith(root) for root in PROD_DATA_WRITE_ROOTS):
        return _is_write_open(fdinfo_text)
    return False


class ProdDataTouched(BaseException):
    """**本会话的进程树**打开过生产数据路径（r7 改判据）。

    同样 `BaseException`：不得被被测代码的 `except Exception` 吞掉。
    """


# ── k61 r7：判据从「文件 mtime 变没变」改成「**本会话**有没有打开过生产路径」──
#
# 为什么必须改（审查者本机实测）：本机有 30+ 个并行 worktree，**另一个会话**
# 在跑它自己的 pytest（`lsof` 抓到它持有生产路径下的文件；空闲 60s 内 mtime
# 自行变了 3 次）→ mtime 判据把**别人写的**记在**我头上** → 定向集**两次都在收尾假红**。
# 而审查者自己的 fd 看门狗（0.05s 采样进程树）**零命中** —— 真凶是并发会话。
# 今晚的全量门禁**必然**撞上这个（并发是常态），所以判据必须能区分"谁写的"。
#
# 新判据（两层，都只看**本会话**）：
#   ① fd 看门狗：0.05s 采样**本会话进程树**（自身 + 后代）的 `/proc/<pid>/fd`，
#      任何 fd 指向生产数据路径即命中；
#   ② `sqlite3.connect(path)` 路径钩子：直接看连接的路径（兜住"打开极短、采样漏拍"）。
# mtime 变化**仍会记录**（作为证据写进报告），但**不再作为失败判据**。

class _ProdFdWatchdog:
    """采样本会话进程树的 fd，判断"生产数据路径有没有被**本会话**打开"。

    `roots` 是**粗筛**（r11 起 = `PROD_DATA_WRITE_ROOTS`，整棵生产树）；
    精确判定（数据存储=打开即命中 / 只读语料=写才命中）在 `_prod_fd_hit`。
    """

    def __init__(self, roots, interval: float = 0.05):
        self.roots = tuple(str(r) for r in roots)
        self.interval = interval
        self.hits: list = []          # [(pid, fd, path)]
        self._stop = threading.Event()
        self._thread = None
        self._pid_cache = []
        self._pid_cache_ts = 0.0

    # ── 进程树 ──
    def _process_tree(self) -> list:
        """自身 + 后代 pid（后代清单每秒刷新一次，采样本身很轻）。"""
        import time as _time
        now = _time.monotonic()
        if now - self._pid_cache_ts < 1.0 and self._pid_cache:
            return self._pid_cache
        mine = {os.getpid()}
        pids = {os.getpid()}
        try:
            entries = []
            for name in os.listdir("/proc"):
                if not name.isdigit():
                    continue
                try:
                    with open(f"/proc/{name}/stat", "rb") as fh:
                        fields = fh.read().split(b") ", 1)[1].split()
                    entries.append((int(name), int(fields[1])))   # (pid, ppid)
                except Exception:
                    continue
            changed = True
            while changed:                     # 逐层展开后代
                changed = False
                for pid, ppid in entries:
                    if ppid in pids and pid not in pids:
                        pids.add(pid)
                        changed = True
        except Exception:
            pass
        self._pid_cache = sorted(pids)
        self._pid_cache_ts = now
        return self._pid_cache

    def _sample_once(self):
        for pid in self._process_tree():
            fd_dir = f"/proc/{pid}/fd"
            try:
                names = os.listdir(fd_dir)
            except OSError:
                continue
            for name in names:
                try:
                    target = os.readlink(os.path.join(fd_dir, name))
                except OSError:
                    continue
                target = target.split(" (deleted)", 1)[0]
                if not any(target.startswith(root) for root in self.roots):
                    continue                      # 粗筛（整棵生产树）
                # r11：只读语料要再看一眼是不是"以写方式打开"（判定见 _prod_fd_hit）。
                # 数据存储不看 fdinfo（打开即命中）→ 不为它们付读 fdinfo 的成本。
                fdinfo = None
                if not any(target.startswith(root) for root in PROD_DATA_ROOTS):
                    fdinfo = _read_fdinfo(pid, name)
                if _prod_fd_hit(target, fdinfo):
                    self.hits.append((pid, name, target))

    def _run(self):
        while not self._stop.is_set():
            try:
                self._sample_once()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="k61-prod-fd-watchdog")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


# 粗筛用**整棵**生产树（判定在 `_prod_fd_hit` 里分两级，见其 docstring）。
_PROD_WATCHDOG = _ProdFdWatchdog(PROD_DATA_WRITE_ROOTS)


_PROD_MTIME_AT_START = _prod_data_mtimes()


def _prod_guard_verdict(hits, mtime_changed) -> str:
    """生产数据守卫的**纯判定**（r8：抽出来供行为锁直接调，别再靠源码文本锁）。

    返回 "" = 通过；否则返回要报的错文。**判据只有 hits**（本会话打开过）；
    mtime 变化单独作为 warning 文案返回给调用方记录（不导致失败）。
    """
    if hits:
        sample = "\n".join(f"  pid={pid} fd={fd} → {path}" for pid, fd, path in hits[:5])
        return ("[k61 生产数据守卫] **本会话进程树**碰了生产数据（数据存储=打开即报；"
                "整棵生产树=以写方式打开才报）：\n" + sample
                + "\n处置：让被测代码走沙箱目录（conftest 已 pin VECTORDB_DIR / "
                  "FORTUNE_DB_PATH / FAISS_INDEX_DIR 到测试目录），不要打开生产库；"
                  "只读语料（books/）若确需引用，请以**只读**方式打开。")
    return ""


# ── k61 r7-5：给沙箱补**最小本地语料**（把 I4 隔离的副作用补回来）──
# I4 把 `VECTORDB_DIR` pin 到空沙箱后，3 条**真语料**用例静默变 skip
# （`test_k24_ref_content_crash` ×2「本地古籍库为空」、`test_dream_engine_g4` ×1
# 「本地向量库为空」），且 `test_engine_run_comparison` 的
# 「真实本地检索前置」不再成立（refs_n=0 / note=检索无命中）。修法：在沙箱里种一个
# **最小**语料集合（集合名 = 权威库名，与生产同名同结构），让这些用例在沙箱下
# 仍能证明原意，而**不碰生产**。成本实测 < 2s（bge-m3 已缓存）。

_SANDBOX_CORPUS_DOCS = [
    "五行相生相克：木生火、火生土、土生金、金生水、水生木；金克木、木克土、土克水、水克火、火克金。",
    "十神者：比肩、劫财、食神、伤官、偏财、正财、七杀、正官、偏印、正印，皆以日主为我而言。",
    "大运十年一换，起运自出生之日起算，顺逆由年干阴阳与性别定。",
    "流年太岁主一年之祸福，与命局冲合害刑，吉凶互见。",
    "用神者，命局之所喜也；扶抑、调候、通关、病药，四法取用。",
    "日主强弱，视月令得气与否；得令者强，失令者弱，再看生扶克泄。",
    "财运看偏正财与食伤，官运看正官七杀，学业看印星，婚姻看夫妻宫。",
    "天乙贵人、文昌、驿马、桃花、羊刃、禄神、将星，皆神煞之要者。",
    "地支六合：子丑合、寅亥合、卯戌合、辰酉合、巳申合、午未合。",
    "地支三合：申子辰合水、亥卯未合木、寅午戌合火、巳酉丑合金。",
    "命局喜用神在木者，宜东方、青色、草木之属；在火者宜南方、赤色。",
    "风水之家，看宅之坐向、门之纳气、山水之形势，以定吉凶。",
    "面相以五官三停十二宫为纲，额主少年，鼻主中年，颏主晚年。",
    "六爻纳甲，以世应定主客，以动爻定事之变迁，六亲配五行而断吉凶。",
    "奇门遁甲，以九宫八门九星八神，配天地人三盘，测事之成败。",
    "紫微斗数以命宫为枢，十二宫分主人生诸事，十四主星各具性情。",
    "择日之法，看建除十二神、二十八宿、黄黑道，避冲煞而取吉时。",
    "合婚以年命生肖、日柱干支、用神互补三者参看，取其相生相合。",
    "姓名之学，五格剖象以笔画数定吉凶，三才配置以五行相生为佳。",
    "梦者，魂之交也；梦见水主财，梦见火主口舌，梦见坠主失位。",
    "解梦之法，先辨梦之类，再察梦之情，后参梦者之境遇。",
    "运势低者宜静守，运势高者宜进取；岁运并临，多有大事。",
    "流月吉凶，以月建与命局之合冲为断，兼看月令之旺衰。",
    "印星为母、为学业、为庇荫；财星为父、为妻财、为实利。",
]


#: 种子语料的**跨会话缓存**：种植要加载 bge-m3（本机高负载时实测 >120s！），
#: 所以**一次种好、之后各会话直接 copytree**（沙箱目录很小）—— 否则每次会话都付这笔钱。
SANDBOX_CORPUS_SEED_DIR = "/dev/shm/k61_seed_corpus"


def _sandbox_collection_count() -> int:
    try:
        import chromadb
        from chromadb.config import Settings as _CS
        from src.book_categories import BOOKS_COLLECTION
        client = chromadb.PersistentClient(path=str(TEST_VECTORDB_DIR),
                                           settings=_CS(anonymized_telemetry=False))
        return client.get_collection(BOOKS_COLLECTION, embedding_function=None).count()
    except Exception:
        return 0


def _seed_sandbox_books_corpus() -> bool:
    """在沙箱向量库里准备最小语料集合（幂等）。

    顺序：沙箱已有 → 直接返回；**种子缓存**有 → copytree 过去（快）；都没有 → 种到缓存
    （慢，每台机器/每次重启只付一次），再 copytree 过去。
    """
    try:
        if _sandbox_collection_count() > 0:
            return True
        os.makedirs(SANDBOX_CORPUS_SEED_DIR, exist_ok=True)
        if _seed_corpus_into(SANDBOX_CORPUS_SEED_DIR):
            shutil.copytree(SANDBOX_CORPUS_SEED_DIR, str(TEST_VECTORDB_DIR),
                            dirs_exist_ok=True)
            return True
        return False
    except Exception as exc:
        logging.getLogger(__name__).warning("[k61] 沙箱最小语料准备失败：%s", exc)
        return False


def _seed_corpus_into(target_dir: str) -> bool:
    """把最小语料种进指定目录（幂等；只在缺失/为空时种）。"""
    try:
        import chromadb
        from chromadb.config import Settings as _CS
        from src.book_categories import BOOKS_COLLECTION
        from src.rag.embedder import Embedder
        from src.rag.retriever import Retriever

        client = chromadb.PersistentClient(path=str(target_dir),
                                           settings=_CS(anonymized_telemetry=False))
        try:
            col = client.get_collection(BOOKS_COLLECTION, embedding_function=None)
            if col.count() > 0:
                return True                      # 已种过
        except Exception:
            pass

        embedder = Embedder(model_name="BAAI/bge-m3")
        embedder.load()
        # 走**合法写入口**（新建实例 + 显式集合名 → writable_collection）——
        # 顺带 dogfood k59/k61 的写路径护栏。
        r = Retriever(str(target_dir), embedder,
                      collection_name=BOOKS_COLLECTION)
        r.writable_collection.upsert(
            ids=[f"k61_seed_{i:03d}" for i in range(len(_SANDBOX_CORPUS_DOCS))],
            documents=list(_SANDBOX_CORPUS_DOCS),
            metadatas=[{"source": "k61 沙箱种子语料", "author": "k61",
                        "category": "bazi_case"} for _ in _SANDBOX_CORPUS_DOCS],
        )
        return True
    except Exception as exc:                     # 种不上不算错：用例会照旧 skip 并写明
        logging.getLogger(__name__).warning(
            "[k61] 沙箱最小语料种植失败（相关用例会 skip）：%s", exc)
        return False


#: 依赖本地语料的测试文件（只有**本次会话确实收集了它们**时才种语料）。
#: 为什么加这个门：种植要**加载 bge-m3**（实测 1~2s，负载高时可到数十秒）——
#: 无差别地对每次会话都种，会让"只跑守卫文件"这类小会话白白付这笔钱。
_CORPUS_DEPENDENT_FILES = {
    "test_k24_ref_content_crash.py",
    "test_dream_engine_g4.py",
    "test_engine_run_comparison.py",
    "test_engine_build_report.py",
}


@pytest.fixture(scope="session", autouse=True)
def _k61_sandbox_books_corpus(request):
    """会话级：**本次收集到依赖语料的文件时**才确保沙箱里有最小本地语料。"""
    names = set()
    for item in getattr(request.session, "items", []) or []:
        try:
            names.add(os.path.basename(str(getattr(item, "fspath", "")) or ""))
        except Exception:
            continue
    if names & _CORPUS_DEPENDENT_FILES:
        _seed_sandbox_books_corpus()
    yield


@pytest.fixture(scope="session", autouse=True)
def _k61_prod_data_guard():
    """会话级：**本会话**打开过生产数据路径 → 报红（r7：不看别人写的 mtime）。

    r11 起还会置上 `PROD_WATCHDOG_ACTIVE_ENV`（**子进程继承**）：让后代会话的 conftest
    知道"有人正在采样我"，从而**不去读生产库建快照**（否则会被本会话的看门狗记成
    "本会话打开过生产数据" → 整轮假红，实测竞态见 §0c ① 的长注）。
    """
    _PROD_WATCHDOG.start()
    os.environ[PROD_WATCHDOG_ACTIVE_ENV] = "1"
    try:
        yield
    finally:
        _PROD_WATCHDOG.stop()
        os.environ.pop(PROD_WATCHDOG_ACTIVE_ENV, None)
        # 证据：mtime 是否变化（**不作为判据** —— 并发会话会写它）
        mtime_changed = [p for p, v in _PROD_MTIME_AT_START.items()
                         if v != _prod_data_mtimes().get(p)]
        verdict = _prod_guard_verdict(list(_PROD_WATCHDOG.hits), mtime_changed)
        if verdict:
            raise ProdDataTouched(verdict)
        if mtime_changed:
            # 只记录：并发会话（本机 30+ worktree）随时可能写这些文件 —— 不是本会话的错。
            logging.getLogger(__name__).warning(
                "[k61 生产数据守卫] 生产数据 mtime 有变化，但**本会话未打开**过它们"
                "（判据已按 r7 改为 fd 看门狗）：%s", mtime_changed)


@pytest.fixture(scope="session", autouse=True)
def _k61_repo_dirt_guard():
    """会话级防复发锁：会话期间**新弄脏**的被跟踪文件 → 会话收尾报红。"""
    before = SESSION_START_DIRTY
    try:
        yield
    finally:
        newly = _scoped_dirty() - before
        if newly:
            raise RepoDirtDetected(
                "[k61 仓库卫生守卫] 测试会话写脏了被 git 跟踪的**运行时产物**：\n"
                + "\n".join(f"  M {p}" for p in sorted(newly))
                + "\n处置：让写方落到 tmp 目录（UserMemory 用 USER_MEMORY_DIR / "
                  "base_dir；run_comparison 用 out_path 参数），不要往仓库内写。"
            )


# ══════════════════════════════════════════════════════════════════
# 1) 常量与异常
# ══════════════════════════════════════════════════════════════════

# ────────────────────────────────────────────────────────────────────────
# ⚠️ **本守卫的固有边界（r8 显式声明，控制方要求；不当作已修）**
#
# 有两类路径**进程内钩子天然覆盖不到**，本守卫**修不了**：
#   ① **起子进程**（子解释器 / curl / 任何 fork+exec 的外部程序）—— 它的 socket
#      在**另一个进程**里，本进程的钩子看不到；
#   ② **ctypes 裸系统调用**（绕过 Python 层直接 `syscall(SYS_sendto, …)` / 直接
#      调 libc 的 `send`)—— 不经过任何 Python 属性查找。
#
# **结构性兜底（这才是敢上线的真凭据）**：`src/config.py:29` 的 `load_env_file`
# 用「**成员判定**」决定是否写入（`if key not in os.environ`），而 k61 r2 的 pin 把
# `DEEPSEEK_API_KEY` 显式设成**空串** → 键**已存在** → `.env` 里的生产 key
# **回填不进来**；且空串**会被子进程继承**（`os.environ` 是进程环境的一部分）。
# ⇒ 即便走子进程/ctypes，**测试进程及其子进程都不持有生产 DeepSeek key**，
#   红线在**结构层**成立；钩子层是"尽早报错"的纵深防御，不是唯一凭据。
# ────────────────────────────────────────────────────────────────────────

#: 禁止出站的域（子串匹配，大小写不敏感）——深寻是**付费生产**模型。
BLOCKED_HOST_MARKERS = ("deepseek",)

#: **免费源白名单（运行期真的被读取** —— r3 修正：r2 之前它只是个死常量，
#: 「看起来有防护」的假象比没有更坏）。语义见 `_EgressGuard._is_allowed_host`：
#: 公网出站**默认拒绝**，只有在白名单里的主机名（及其解析出的 IP）才放行；
#: 回环/私网/链路本地地址一律放行（不涉公网暴露）。
#: 每条必须带理由（锁用例强制：`tests/test_k61_llm_egress_guard.py`）。
FREE_LLM_HOSTS = {
    "open.bigmodel.cn": "智谱开放平台 —— 用户指定的**免费**测试 LLM（glm-4-flash），"
                        "真实端到端用例的唯一合法 LLM 出口",
}

#: 非 LLM 的合法测试出站（只读可达性探测，与 LLM 无关）。同样必须带理由。
NON_LLM_EGRESS_HOSTS = {
    "cn.bing.com": "web_search_available() 的可达性探测（免费搜索通道，无 key）；"
                   "仓内 k15 冒烟用它判断搜索工具可用性。只发一次 HEAD/GET 探测。",
}

#: 运行期白名单（= 免费 LLM 源 + 非 LLM 探测源）。**这是 `_is_allowed_host` 读的那份。**
#: 另有**自动派生**的一份：`src/rag/web_search.py` 里声明的免费搜索通道
#: （`cn.bing.com` / `www.so.com` / `www.baidu.com` / `www.sogou.com` /
#: `open.bigmodel.cn`）。派生而非手抄，是为了「代码加了搜索源、守卫自动跟上」，
#: 也避免手抄漏一个就把合规的搜索用例打死（r3 实测：test_bot.py 的搜索链路
#: 真打 360 搜索 → 被默认拒绝层拦下，属误伤）。
ALLOWED_EGRESS_HOSTS = {**FREE_LLM_HOSTS, **NON_LLM_EGRESS_HOSTS}

_SEARCH_HOSTS_CACHE = None


def _derived_search_hosts() -> dict:
    """从 `src/rag/web_search.py` 的 `*_URL(S)` 常量派生「代码声明的搜索通道」。

    ⚠️ 只能**缓存非空结果**：若在某次 `src.rag.web_search` 尚未初始化完（模块在
    sys.modules 里但 `*_URL` 常量还没绑定）时算过一次并缓存空表，之后所有搜索通道
    都会被判成"不在白名单" → 合规的搜索用例被默认拒绝层打死（r3 实测踩到：
    `www.so.com` 的解析结果被拒，链路是 `_guard_getaddrinfo` 在收集期被触发）。
    因此：算不到就**不缓存**，等模块就绪后重算。
    """
    global _SEARCH_HOSTS_CACHE
    if _SEARCH_HOSTS_CACHE:
        return _SEARCH_HOSTS_CACHE
    hosts = {}
    try:
        from urllib.parse import urlparse
        from src.rag import web_search as ws
        if not any(n.endswith("_URL") for n in vars(ws)):
            return _SEARCH_HOSTS_CACHE or {}      # 模块还没初始化完：不缓存、直接返回
        for name in dir(ws):
            if not (name.endswith("_URL") or name.endswith("_URLS")):
                continue
            val = getattr(ws, name)
            urls = [val] if isinstance(val, str) else list(val or [])
            for u in urls:
                h = urlparse(str(u)).hostname
                if h:
                    why = f"src/rag/web_search.py::{name}（代码声明的免费搜索通道）"
                    hosts[h.lower()] = why
                    # 同时放行「去掉 www. 的注册域」：搜索站会 302 到 m./www. 等兄弟主机
                    # （`follow_redirects=True`），只放行字面 `www.so.com` 会漏掉
                    # `so.com` / `m.so.com` → 合规搜索被默认拒绝层打死（r3 实测踩到）。
                    if h.lower().startswith("www."):
                        hosts[h.lower()[4:]] = why + "（含去 www. 的兄弟主机）"
    except Exception:          # 导入失败/离线包 → 派生为空，不影响守卫其余部分
        hosts = {}
    if hosts:
        _SEARCH_HOSTS_CACHE = hosts
    return hosts

#: 代理环境变量名（有代理时"谁在被连"要看 CONNECT 目标，不是 socket 地址）。
PROXY_ENV_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                  "https_proxy", "http_proxy", "all_proxy")

#: **不透明代理** scheme（k61 r5 的 C1）：`https://` 代理把 CONNECT 目标藏在 **TLS
#: 之内**（`socks*` 同理藏在握手之后）→ 我们**唯一的判据被藏起来了**。
#: 处置（控制方定的方向）：**fail-closed** —— 走这类代理的连接一律拒绝，
#: 因为"看不见目标"就没有"看得见再判"的余地。
#: （r3 的 D 层明文扫描对这类代理**天然无效**：审查者实测
#:   `HTTPS_PROXY=https://…` 时 `requests` 把 `CONNECT api.deepseek.com:443`
#:   送出而守卫零反应 —— 报告里"隧道目标同样受判"对加密代理不成立。）
OPAQUE_PROXY_SCHEMES = ("https", "socks", "socks5", "socks5h", "socks4", "socks4a")

#: `glm_route` 夹具强制使用的免费模型。
FREE_LLM_MODEL = "glm-4-flash"


class EgressBlocked(BaseException):
    """测试进程发起了**不被允许**的真实出站（r3 起含"公网默认拒绝"）。

    故意继承 `BaseException`：被测代码的 `except Exception` 兜底降级
    （advisor_v2 / mood_detector / jian_quote / night_soliloquy 均有）
    不得把这条守卫失败吞掉，否则守卫形同虚设。
    """


class DeepSeekEgressBlocked(EgressBlocked):
    """对 deepseek 域的真实出站（k61 红线，r1 起的专用类型）。"""


class PublicEgressBlocked(EgressBlocked):
    """对**非白名单公网地址**的真实出站（r3：IP 字面量绕过 + 代理穿网）。

    为什么要有这一类：r2 之前守卫只按**主机名**判定，审查者实测两条绕过 ——
    ① `HTTPS_PROXY` 在场时 socket 层看到的是**代理地址**（socket 层失明），
       四路（requests/urllib/httpx/aiohttp）全部静默穿网；
    ② 直接用 **IP 字面量** 连接（无主机名可判）。
    修法：判定下沉到"真正建立连接/写出目标"的地方，并对**解析后的地址**判定。
    """


def _is_blocked(host) -> bool:
    h = str(host or "").lower()
    return any(m in h for m in BLOCKED_HOST_MARKERS)


def _is_allowed_host(host) -> bool:
    """主机名是否放行（后缀匹配，允许子域）：静态白名单 + 代码声明的搜索通道。

    匹配规则：`h == 条目` 或 `h` 是条目的子域（`h.endswith("." + 条目)`）。

    ⚠️ k61 r5（I1）：**绝不允许"由待判主机名反向自匹配"**。r3 曾在这里写过
    `if h.startswith("www."): names.append(h[4:])` —— 那是一条**过宽的默认放行**：
    任意 `www.*` 主机都被判在白名单（`www.evil.com` / `www.baidu.com.evil.tld` /
    `WWW.Evil.COM` 全 True），并**污染 IP 学习集**（学到 `www.x → 1.2.3.4` 之后，
    `connect(("1.2.3.4", 443))` 直接放行）。"去掉 www." 的等价形式**只能**在
    **白名单条目**那一侧派生（见 `_derived_search_hosts()`：条目是 `www.so.com`
    时额外登记 `so.com`），待判主机名不得反过来扩大白名单。
    """
    h = str(host or "").lower().rstrip(".")
    if not h:
        return False
    names = list(ALLOWED_EGRESS_HOSTS) + list(_derived_search_hosts())
    return any(h == a or h.endswith("." + a) for a in names)


def _parse_proxy_env() -> list:
    """解析代理环境变量 → [(scheme, host, port), …]（scheme 已小写）。"""
    import urllib.parse
    out = []
    for var in PROXY_ENV_VARS:
        raw = os.environ.get(var, "").strip()
        if not raw:
            continue
        if "://" not in raw:
            raw = "http://" + raw
        try:
            u = urllib.parse.urlparse(raw)
        except Exception:
            continue
        if u.hostname:
            out.append(((u.scheme or "http").lower(), u.hostname.lower(), u.port))
    return out


def _proxy_hosts() -> set:
    """当前环境变量里配置的代理主机名（明文代理是**通道**，放行；目标由 CONNECT 行判）。"""
    return {h for scheme, h, _ in _parse_proxy_env()
            if scheme not in OPAQUE_PROXY_SCHEMES}


def _opaque_proxy_endpoints() -> set:
    """**不透明代理**的 {主机名, 端口}（TLS/SOCKS：目标看不见 → fail-closed）。"""
    return {(h, p) for scheme, h, p in _parse_proxy_env()
            if scheme in OPAQUE_PROXY_SCHEMES}


def _is_non_public_ip(ip: str) -> bool:
    """回环/私网/链路本地/保留地址 —— 不涉公网暴露，一律放行。"""
    import ipaddress
    raw = str(ip).strip().strip("[]").split("%")[0]
    if raw.count(":") == 1:                   # host:port（防御性：调用方可能没剥端口）
        raw = raw.rsplit(":", 1)[0]
    try:
        a = ipaddress.ip_address(raw)
    except ValueError:
        return False
    return not a.is_global


class _EgressGuard:
    """安装/卸载出站守卫，并记录违规（即使异常被吞也能在 session 收尾报红）。

    ## r3 拦截面（五层，自下而上）

    | 层 | 钩子 | 覆盖 |
    |---|---|---|
    | A | `socket.getaddrinfo` | 全部按域名连接的库（直连） |
    | B | `socket.create_connection` | 标准库同步路径 |
    | C | `socket.socket.connect` / `connect_ex` | **按解析后的 IP 判定**：非白名单公网一律拒（IP 字面量绕不过去） |
    | D | `socket.socket.send` / `sendall`（只扫**首包**） | **代理 CONNECT 目标**（`CONNECT host:port` / `Host:` / 绝对 URI）—— 代理在场时 socket 地址是代理，只有首包能看见真实目标 |
    | E | httpx 真实传输层 | httpx 精确 URL + 明确报错 |

    为什么需要 C：r2 只按主机名判，审查者实测**用 IP 字面量直接连**（无主机名）可绕过。
    为什么需要 D：r2 只按 socket 地址判，**设了 `HTTPS_PROXY` 时地址是代理** →
    requests/urllib/httpx/aiohttp 四路可静默穿网（k61 r3 实测：监听器收到
    `CONNECT api.deepseek.com:443` ×3）。`CONNECT`/`Host`/绝对 URI 都是**明文**，
    在 TLS 之前写出，所以扫首包就能看见真实目标，与用哪个 HTTP 库无关。
    """

    def __init__(self):
        self.violations = []
        self.hook_errors = []          # r8：钩子自身异常（必须响亮）
        self._orig_getaddrinfo = None
        self._httpx = None
        self._lock = threading.Lock()
        #: 白名单主机解析出的 IP（会话内学习）——C 层据此放行合法目标。
        self._allowed_ips = set()
        #: 不透明代理主机解析出的 IP（C1：这些 IP:port 的连接一律 fail-closed）。
        self._opaque_proxy_ips = set()
        #: **代理主机**解析出的 IP（明文代理是通道 → C 层放行；**但不作为 TLS 目标放行**）。
        self._proxy_ips = set()
        #: C2：按 fd 键控的写入前缀缓冲（socket 对象无 __dict__，故用 fd）
        self._write_bufs = {}
        #: 已知 socket fd（`os.write` 钩子据此判断"这是不是网络写"）
        self._socket_fds = set()
        self._sock_fd = weakref.WeakKeyDictionary()   # socket → fd
        self._fd_owner = {}                            # fd → socket（weakref，查复用）

    # ── C2：fd 键 ──
    def _sock_key(self, sock):
        """取 socket 的 fd 作为缓冲键（并登记为网络 fd）。

        ⚠️ **fd 会被复用**：同一个 fd 可能先后属于不同 socket（对象销毁后 fd 归还）。
        若不复位，前一个连接的累积缓冲会串到新连接上 —— 实测表现为**顺序相关**的
        假红/漏判（k61 r5 自测踩到：同一测试单独跑绿、与同类的上一个用例一起跑就红）。
        故：fd 的属主变了就**清空**该 fd 的缓冲。
        """
        try:
            fd = self._sock_fd.get(sock)
            if fd is None:
                fd = sock.fileno()
                self._sock_fd[sock] = fd
                self._socket_fds.add(fd)
            owner = self._fd_owner.get(fd)
            if owner is not sock:
                self._fd_owner[fd] = sock
                with self._lock:
                    self._write_bufs.pop(fd, None)
            return fd
        except Exception:
            return None

    def _is_socket_fd(self, fd) -> bool:
        """该 fd 是不是**真的** socket（权威判定；避免把文件写当网络写扫）。"""
        try:
            import stat as _stat
            return _stat.S_ISSOCK(os.fstat(fd).st_mode)
        except Exception:
            return False

    def _reset_write_buf(self, sock):
        """新连接建立时重置该 fd 的写缓冲（fd 会被复用，避免串味）。"""
        key = self._sock_key(sock)
        if key is None:
            return
        with self._lock:
            self._write_bufs.pop(key, None)

    # ── C1：不透明代理判定 ──
    def _ip_learned(self, ip) -> bool:
        """该 IP 是否来自**白名单主机**的解析（TLS 判据用，**不豁免回环/私网，也不认代理**）。

        为什么不认代理：代理是**通道**，允许它建 TCP（明文代理才有可判的 CONNECT 行），
        但对它发客户端 TLS 会把真实目标藏住 —— 那正是 C1 要拒的形态。
        r7 实测踩到：把代理 IP 也算"learned"后，显式 loopback 代理重新变成放行。
        """
        with self._lock:
            return str(ip) in self._allowed_ips

    def _is_opaque_proxy_target(self, host, port=None) -> bool:
        """目标是否为**不透明代理**端点（TLS/SOCKS：目标看不见 → fail-closed）。"""
        ends = _opaque_proxy_endpoints()
        if not ends:
            return False
        h = str(host or "").lower()
        for p_host, p_port in ends:
            if port is not None and p_port is not None and int(port) != int(p_port):
                continue
            if h == p_host:
                return True
            with self._lock:
                if h in self._opaque_proxy_ips:      # 该 IP 是从代理主机解析出来的
                    return True
        return False

    def _note_opaque_proxy_ip(self, host, sockaddr):
        try:
            ends = _opaque_proxy_endpoints()
            if not ends:
                return
            ip = sockaddr[0] if isinstance(sockaddr, (tuple, list)) else sockaddr
            if ip is None:
                return
            if any(str(host or "").lower() == p_host for p_host, _ in ends):
                with self._lock:
                    self._opaque_proxy_ips.add(str(ip))
        except Exception:
            pass

    # ── 记录 ──
    def _note_hook_error(self, where: str, exc: Exception):
        """钩子**自己**抛异常 → 记账（会话收尾报红）。

        r7 自曝过：钩子里的 `except Exception: pass` 把"self 绑定错"这类**钩子失效**
        吞成静默，违规计数恒 0 → **拦不住与没挂钩分不清**。钩子出错必须响亮。
        """
        with self._lock:
            self.hook_errors.append(f"{where}: {type(exc).__name__}: {str(exc)[:120]}")

    def _trip(self, where: str, host: str, extra: str = "",
              exc: type = DeepSeekEgressBlocked, rule: str = ""):
        rule = rule or (f"测试一律不得打 DeepSeek（付费/生产）；"
                        f"需要真实 LLM 请用 `glm_route` 夹具走免费 {FREE_LLM_MODEL}，"
                        f"只需验证形状请用 httpx.MockTransport。")
        detail = (f"[k61 守卫] 测试进程发起不被允许的真实出站 → 已拦截。\n"
                  f"  拦截层: {where}\n  目标: {host}\n{extra}"
                  f"  规则: {rule}\n"
                  f"  公网白名单: {', '.join(sorted(ALLOWED_EGRESS_HOSTS))}"
                  f"（回环/私网地址不受限）")
        with self._lock:
            self.violations.append(detail)
        raise exc(detail)

    # ── 判定 ──
    def _learn_allowed_ip(self, host, family, sockaddr):
        """白名单/代理主机的解析结果 → 记进放行集（C 层用）。"""
        try:
            ip = sockaddr[0] if isinstance(sockaddr, (tuple, list)) else sockaddr
        except Exception:
            return
        if ip is None:
            return
        if _is_allowed_host(host):
            with self._lock:
                self._allowed_ips.add(str(ip))
        if str(host or "").lower() in _proxy_hosts():
            with self._lock:
                self._proxy_ips.add(str(ip))

    def _ip_allowed(self, ip) -> bool:
        """C 层放行判据：非公网 / 白名单主机学到的 IP / **代理主机**学到的 IP。"""
        if _is_non_public_ip(ip):
            return True
        with self._lock:
            return str(ip) in self._allowed_ips or str(ip) in self._proxy_ips

    # ── 安装 ──
    def install(self):
        if getattr(self, "_orig_getaddrinfo", None) is not None:
            return                      # 幂等：模块级已装则跳过
        self._orig_getaddrinfo = socket.getaddrinfo
        self._orig_create_connection = socket.create_connection
        self._orig_connect = socket.socket.connect
        self._orig_connect_ex = socket.socket.connect_ex
        self._orig_send = socket.socket.send
        self._orig_sendall = socket.socket.sendall
        self._orig_sendmsg = socket.socket.sendmsg
        self._orig_sendto = socket.socket.sendto
        self._orig_os_write = os.write
        self._orig_os_writev = os.writev
        self._orig_wrap_socket = ssl.SSLContext.wrap_socket
        self._orig_wrap_bio = ssl.SSLContext.wrap_bio
        ssl.SSLContext.wrap_socket = _guard_wrap_socket
        ssl.SSLContext.wrap_bio = _guard_wrap_bio
        # r8（C2）：**同族别名** —— `os.write is posix.write` 为 True，但它们是
        # **两个模块属性**：只补 os.* 时 `posix.write(fd, …)` 仍能把 CONNECT 送出。
        self._orig_posix_write = posix.write
        self._orig_posix_writev = posix.writev
        self._orig_posix_sendfile = posix.sendfile
        self._orig_os_sendfile = os.sendfile
        self._orig_sock_sendfile = socket.socket.sendfile
        posix.write = _guard_os_write
        posix.writev = _guard_os_writev
        posix.sendfile = _guard_os_sendfile
        os.sendfile = _guard_os_sendfile
        socket.socket.sendfile = _guard_sock_sendfile
        # r9（C2 ③）：`os.splice` / `posix.splice` —— **同族别名**（两个模块属性、同一函数）。
        # 它既不是 send 也不是 write 起头 → r8 的"按前缀猜"漏了它（实测明文出线）。
        self._orig_os_splice = os.splice
        self._orig_posix_splice = posix.splice
        os.splice = _guard_os_splice
        posix.splice = _guard_os_splice
        # r9：`eventfd_write`（Linux 独有；不在时跳过 —— 平台上没有这个通道）。
        self._orig_eventfd_write = getattr(os, "eventfd_write", None)
        self._orig_posix_eventfd_write = getattr(posix, "eventfd_write", None)
        if self._orig_eventfd_write is not None:
            os.eventfd_write = _guard_eventfd_write
            posix.eventfd_write = _guard_eventfd_write
        # r9（C2）：TLS 入口的同族面 —— `_create`（Python 类方法）+ C 层 `_wrap_*` 的**遮蔽层**
        # （`ssl.SSLContext` 是 `_ssl._SSLContext` 的 Python 子类 → 子类属性可遮蔽继承来的
        #  C 方法；`_ssl._SSLContext._wrap_*` 本身不可赋值，那是声明的残留）。
        # ⚠️ 存**原 classmethod 对象**（`__dict__` 里那份）而不是绑定方法：还原后语义逐字相同。
        self._orig_sslsocket_create = ssl.SSLSocket.__dict__["_create"]
        self._orig_sslobject_create = ssl.SSLObject.__dict__["_create"]
        ssl.SSLSocket._create = classmethod(_guard_sslsocket_create)
        ssl.SSLObject._create = classmethod(_guard_sslobject_create)
        self._orig_ctx_wrap_socket = ssl.SSLContext._wrap_socket      # 解析后的 C 方法（继承来的）
        self._orig_ctx_wrap_bio = ssl.SSLContext._wrap_bio
        #: 类字典里**原本**有没有这两个名字（实测：没有，都是继承 `_ssl._SSLContext` 的）。
        #: 卸载时要按"原本有没有"决定「delattr 还原继承」还是「赋回原属性」—— 不靠假设。
        self._own_ctx_wrap_socket = ssl.SSLContext.__dict__.get("_wrap_socket")
        self._own_ctx_wrap_bio = ssl.SSLContext.__dict__.get("_wrap_bio")
        ssl.SSLContext._wrap_socket = _guard_ctx_wrap_socket           # 遮蔽（子类属性）
        ssl.SSLContext._wrap_bio = _guard_ctx_wrap_bio
        # r9（I1）：把 socket fd 包成"文件对象"的族（C 层直写 fd，钩子看不见内容）。
        self._orig_fdopen = os.fdopen
        self._orig_io_open = io.open
        self._orig_builtins_open = builtins.open
        self._orig_fileio = io.FileIO
        self._orig_copyfileobj = shutil.copyfileobj
        os.fdopen = _guard_fdopen
        io.open = _guard_io_open
        builtins.open = _guard_io_open
        io.FileIO = _GuardedFileIO
        shutil.copyfileobj = _guard_copyfileobj
        socket.getaddrinfo = _guard_getaddrinfo
        socket.create_connection = _guard_create_connection
        socket.socket.connect = _guard_connect
        socket.socket.connect_ex = _guard_connect_ex
        socket.socket.send = _guard_send
        socket.socket.sendall = _guard_sendall
        socket.socket.sendmsg = _guard_sendmsg
        socket.socket.sendto = _guard_sendto
        os.write = _guard_os_write
        os.writev = _guard_os_writev
        try:
            import httpx
        except Exception:  # pragma: no cover - httpx 是硬依赖，走不到
            self._httpx = None
            return
        self._httpx = httpx
        self._orig_httpx_sync = httpx.HTTPTransport.handle_request
        self._orig_httpx_async = httpx.AsyncHTTPTransport.handle_async_request
        httpx.HTTPTransport.handle_request = _guard_httpx_sync
        httpx.AsyncHTTPTransport.handle_async_request = _guard_httpx_async

    def uninstall(self):
        if getattr(self, "_orig_getaddrinfo", None) is None:
            return
        socket.getaddrinfo = self._orig_getaddrinfo
        socket.create_connection = self._orig_create_connection
        socket.socket.connect = self._orig_connect
        socket.socket.connect_ex = self._orig_connect_ex
        socket.socket.send = self._orig_send
        socket.socket.sendall = self._orig_sendall
        socket.socket.sendmsg = self._orig_sendmsg
        socket.socket.sendto = self._orig_sendto
        os.write = self._orig_os_write
        os.writev = self._orig_os_writev
        ssl.SSLContext.wrap_socket = self._orig_wrap_socket
        ssl.SSLContext.wrap_bio = self._orig_wrap_bio
        posix.write = self._orig_posix_write
        posix.writev = self._orig_posix_writev
        posix.sendfile = self._orig_posix_sendfile
        os.sendfile = self._orig_os_sendfile
        socket.socket.sendfile = self._orig_sock_sendfile
        os.splice = self._orig_os_splice
        posix.splice = self._orig_posix_splice
        if getattr(self, "_orig_eventfd_write", None) is not None:
            os.eventfd_write = self._orig_eventfd_write
            posix.eventfd_write = self._orig_posix_eventfd_write
        ssl.SSLSocket._create = self._orig_sslsocket_create
        ssl.SSLObject._create = self._orig_sslobject_create
        # 遮蔽层还原：原本是**继承来的** → `del` 掉子类属性（重新解析到 `_ssl._SSLContext`）；
        # 原本类字典里就有 → 赋回原属性。
        for _name, _own in (("_wrap_socket", getattr(self, "_own_ctx_wrap_socket", None)),
                            ("_wrap_bio", getattr(self, "_own_ctx_wrap_bio", None))):
            try:
                if _own is not None:
                    setattr(ssl.SSLContext, _name, _own)
                else:
                    delattr(ssl.SSLContext, _name)
            except Exception:      # pragma: no cover - 理论上不成立
                pass
        os.fdopen = self._orig_fdopen
        io.open = self._orig_io_open
        builtins.open = self._orig_builtins_open
        io.FileIO = self._orig_fileio
        shutil.copyfileobj = self._orig_copyfileobj
        if getattr(self, "_httpx", None) is not None:
            self._httpx.HTTPTransport.handle_request = self._orig_httpx_sync
            self._httpx.AsyncHTTPTransport.handle_async_request = self._orig_httpx_async
        self._orig_getaddrinfo = None

    # ── D 层：明文请求头扫描（代理 CONNECT / Host / 绝对 URI）──
    #: 每个 socket（按 fd 键控）已累积的写入前缀（k61 r5 的 C2）。
    #: r3 只看"单块写的前 64 字节像不像请求头" → **分片写可绕过**
    #: （审查者实测：分片 / memoryview / sendmsg / os.write 四种全部把
    #:  `CONNECT api.deepseek.com:443` 送出而守卫静默）。改为**跨块累积判定**：
    #: 只要累积前缀"看起来以请求头开头"，就对累积内容做目标判定。
    _WRITE_BUF_CAP = 4096

    def scan_payload(self, sock, data, fd=None):
        """跨块累积判定：把写入追加进该连接的缓冲，再判**累积前缀**。

        - 只判"累积前缀以请求头开头"的连接（`CONNECT `/`GET `/…/`Host:`）：
          不判 body，避免载荷类用例假红（有专门用例）。
        - **跨块**：分片写（`send(b"CON")` + `send(b"NECT …")`）累积后照样命中。
        - `memoryview`/`bytearray` 一并归一化为 bytes（r3 只认 bytes/bytearray → 漏）。
        - 缓冲按 **fd** 键控，`connect` 时重置（fd 会被复用时避免串味）。
        """
        try:
            payload = bytes(data)
        except Exception:
            return
        if not payload:
            return
        key = fd if fd is not None else self._sock_key(sock)
        if key is None:
            return
        with self._lock:
            buf = self._write_bufs.get(key)
            if buf is None:
                buf = bytearray()
                self._write_bufs[key] = buf
            if len(buf) < self._WRITE_BUF_CAP:
                buf.extend(payload[: self._WRITE_BUF_CAP - len(buf)])
            head = bytes(buf[:256]).decode("latin-1", "replace")
            if not _looks_like_request_head(head):
                return
            text = bytes(buf).decode("latin-1", "replace")
        # r7：扫描**整个请求头区**（到首个空行为止）而不是固定前 8 行 ——
        # 实测漏过 `Host:` 排在第 9 行的形态（92 字节出线）。
        head_region = re.split(r"\r?\n\r?\n", text, maxsplit=1)[0]
        for line in head_region.split("\r\n")[:48]:
            target = _target_host_in_line(line)
            if not target:
                continue
            if _is_blocked(target):
                self._trip("socket 明文请求头（代理 CONNECT / Host 行）", target,
                           f"  明文首行: {line[:160]!r}\n",
                           rule="测试一律不得打 DeepSeek（付费/生产）—— "
                                "代理（HTTPS_PROXY）只是通道，CONNECT 目标同样受本守卫约束。")
            if not _is_allowed_host(target) and not _is_non_public_ip(target):
                if not any(target.lower() == h for h in _proxy_hosts()):
                    self._trip("socket 明文请求头（代理 CONNECT / Host 行）", target,
                               f"  明文首行: {line[:160]!r}\n",
                               exc=PublicEgressBlocked,
                               rule="测试不得对白名单外的公网主机发起真实出站"
                                    "（白名单见下方；如需新增请连同理由加进 "
                                    "ALLOWED_EGRESS_HOSTS / NON_LLM_EGRESS_HOSTS）。")


#: 「这段明文像不像 HTTP 请求头」的判据（只按这个扫明文，避免扫 body 假红）。
#: r7 放宽了两处（实测漏过）：**方法名不限字符集/长度**（`M-SEARCH` / 自定义动词）、
#: **`Host:` 不要求在第 1 行**（此前只看前 64 字节 → 第 9 行的 Host 漏判）。
_REQUEST_HEAD_RE = re.compile(
    r"^(?:\S{1,32}\s+\S+)\s+HTTP/1\.[01]|^Host\s*:",
    re.IGNORECASE | re.MULTILINE,
)
_TLS_HANDSHAKE_PREFIXES = (b"\x16\x03",)      # TLS record（客户端问候）——不是请求头


def _looks_like_request_head(text: str) -> bool:
    """明文前缀是否像 HTTP 请求头（r7：方法名不限、Host 不限行号、但**不扫 body**）。"""
    head_region = re.split(r"\r?\n\r?\n", text, maxsplit=1)[0]
    return bool(_REQUEST_HEAD_RE.search(head_region))


def _target_host_in_line(line: str):
    """从一行明文里提取目标主机名：`CONNECT h:p` / `Host: h` / `METHOD http://h/...`。

    **必须去掉端口再返回**：`Host: 127.0.0.1:18877` 这种带端口的写法极常见，
    若把 `:18877` 一起交给 IP 判定，`ipaddress.ip_address("127.0.0.1:18877")`
    会抛 ValueError → 被当成"非私网" → 误伤本地服务（r3 实测踩到：本地 TTS 桩被拦）。
    """
    s = (line or "").strip()
    if not s:
        return None
    low = s.lower()
    if re.match(r"^connect\s", low):                      # r7：tab 分隔同样算
        target = s.split(None, 1)[1].split()[0]
        return _strip_port(target)
    if re.match(r"^host\s*:", low):
        return _strip_port(s.split(":", 1)[1].strip())
    # r7：方法名不再限 3~10 个字母（`M-SEARCH` / `X_CUSTOM_VERB` 这类同样要认）
    m = re.match(r"^\S{1,32}\s+https?://([^/\s]+)", s)   # r7：方法名不限字符集（`M-SEARCH*`）
    if m:
        return _strip_port(m.group(1))
    return None


def _strip_port(target: str) -> str:
    """去掉 `host:port` 的端口；IPv6 字面量（`[::1]:443`）保留方括号内内容。"""
    t = (target or "").strip()
    if t.startswith("["):                     # [::1]:443
        return t.split("]", 1)[0].lstrip("[")
    if t.count(":") == 1:                     # host:port（IPv4/域名）
        return t.rsplit(":", 1)[0]
    return t                                  # 裸 IPv6 或纯主机名


_GUARD = _EgressGuard()


# ── 模块级替身：必须以「裸函数」形态挂到 socket / httpx 上 ──
# （把 bound method 赋成类属性时描述符协议不生效，调用方少传一个 self →
#  TypeError；这些替身在被调用时才解析模块级 `_GUARD`，故顺序无碍。）
def _guard_getaddrinfo(host, *a, **kw):
    if _is_blocked(host):
        _GUARD._trip("socket.getaddrinfo", host)
    if host and not _is_allowed_host(host) and not _is_non_public_ip(host) \
            and str(host).lower() not in _proxy_hosts():
        # 域名不在白名单 → 先让解析发生（可能解析失败/离线），拿到 IP 后由 C 层拒。
        pass
    infos = _GUARD._orig_getaddrinfo(host, *a, **kw)
    for info in infos or []:
        try:
            _GUARD._learn_allowed_ip(host, info[0], info[4])
            _GUARD._note_opaque_proxy_ip(host, info[4])
        except Exception:
            continue
    return infos


def _guard_create_connection(address, *a, **kw):
    host = address[0] if isinstance(address, (tuple, list)) and address else address
    if _is_blocked(host):
        _GUARD._trip("socket.create_connection", host)
    return _GUARD._orig_create_connection(address, *a, **kw)


def _guard_connect(sock, address, *a, **kw):
    _guard_check_address("socket.socket.connect", address)
    _GUARD._reset_write_buf(sock)          # C2：新连接 → 重置写缓冲
    return _GUARD._orig_connect(sock, address, *a, **kw)


def _guard_connect_ex(sock, address, *a, **kw):
    _guard_check_address("socket.socket.connect_ex", address)
    _GUARD._reset_write_buf(sock)
    return _GUARD._orig_connect_ex(sock, address, *a, **kw)


def _guard_check_address(where, address):
    """C 层：按**解析后的目标地址**判定（IP 字面量也走这里）。"""
    ip = address[0] if isinstance(address, (tuple, list)) and address else address
    if ip is None:
        return
    ip = str(ip)
    port = address[1] if isinstance(address, (tuple, list)) and len(address) > 1 else None
    if _is_blocked(ip):
        _GUARD._trip(where, ip)
    if _GUARD._is_opaque_proxy_target(ip, port):
        _GUARD._trip(
            where, f"{ip}:{port}",
            extra="  判据: 目标是**不透明代理**（scheme ∈ "
                  f"{', '.join(OPAQUE_PROXY_SCHEMES)}）—— 代理之下的真实目标被 TLS/SOCKS "
                  "握手藏住，测试进程**无从判别**，故 fail-closed。\n",
            exc=PublicEgressBlocked,
            rule="测试进程对**不透明代理**一律 fail-closed：加密/SOCKS 代理把唯一判据"
                 "（CONNECT 目标）藏起来了，看不见就无法判 → 拒绝。"
                 "若确需走代理，请改用**明文** `http://` 代理（CONNECT 行可判）。")
    if not _GUARD._ip_allowed(ip):
        _GUARD._trip(where, ip, exc=PublicEgressBlocked,
                     rule="测试不得连接白名单外的**公网地址**（这条按解析后的 IP 判定，"
                          "所以 IP 字面量同样拦得住）。若确需公网出站，请把它连同理由"
                          "加进 ALLOWED_EGRESS_HOSTS / NON_LLM_EGRESS_HOSTS。")


def _as_scannable_payload(data):
    """把任意**缓冲协议对象**归一化成 `bytes` 交给扫描器；真不是缓冲协议才返回 `None`。

    ⚠️ **r10 C1 的根因，别再退回类型白名单**：r9 之前这里写的是
    `isinstance(data, (bytes, bytearray, memoryview))` —— 那是**类型白名单**，
    凡是白名单**外面**的缓冲协议对象（`mmap`、`array.array`、`ctypes` buffer、
    第三方 C 扩展暴露的 buffer…）**整个跳过扫描、直送网线**。
    实测（r9 对抗审查 A1–A5）：`sendall(mmap)` / `send(mmap)` / `os.write(fd, mmap)` /
    `sendall(array.array('B'))` / `sendall(ctypes buffer)` 五条**全部** NO-BLOCK、
    `reached=True`，而**同族**的 `_guard_sendmsg`（`bytes(b)` 归一化）与
    `_guard_os_writev` 却拦得住 —— 同一个守卫里"一半做了一半没做"，
    且 `scan_payload` 自己开头就写了 `bytes(data)` 防御，却被这层 isinstance 门
    **挡在门外**，纯属自相矛盾。

    为什么"类型白名单"这个写法本身就是错的：缓冲协议是**开放集合**（任何 C 扩展都能
    注册 `bf_getbuffer`），白名单天然**只能枚举已知的**。绊线（`_TRIPWIRE_RE`）防的是
    "新名字"，结构上防不了"新类型"—— 所以判据必须从"是不是我认识的那几种类型"
    改成"**能不能转成 bytes**"：能转就扫，转不了（`str` / `int` 之外的普通对象）才是
    真的不是载荷、放行。

    `bytes(data)` 对 `int` **不抛异常**（返回 `bytes(n)` 个零字节），这是无害的：
    零字节不像请求头，扫描器不会误判（`scan_payload` 只看"像不像请求头明文"）。

    ⚠️ **r11 I-1**：`bytes()` 的失败**不止 TypeError**。这几种都**不是**守卫故障，
    而是"这块载荷扫不了"，必须与 `str`/普通对象**同样放行**：
      - `mmap` 已 close：`ValueError: mmap closed or invalid`；
      - `memoryview` 已 release：`ValueError: operation forbidden on released memoryview object`；
      - 自定义 `__bytes__` 抛 `ValueError`；
      - 并发下 buffer 大小/形状变了：`BufferError`。
    r10 只吞 TypeError → 这些会落到钩子的 `except Exception` → `_note_hook_error`
    → **会话收尾把整轮报 ERROR**，报文还谎称"守卫可能已静默失效"；而实际行为与
    **不挂钩的裸 Python 逐字相同**（异常原样抛给调用方、`violations` 恒 0、无误拦）。
    "守卫报错了"与"守卫没问题"在这条路径上被写反了 —— 归到"不可扫 → 放行"才是真相。
    """
    try:
        return bytes(data)
    except TypeError:
        return None          # 真不可转换（str / 普通对象…）→ 放行
    except (ValueError, BufferError):
        return None          # r11 I-1：不可扫（closed mmap / released memoryview…）→ 放行


def _join_scannable_buffers(buffers):
    """把 `sendmsg` / `writev` 的**缓冲列表**并成 `bytes` 交给扫描器。

    r11 I-1：任何一块"扫不了"（见 `_as_scannable_payload` 的 ValueError/BufferError）
    就**整块放行**（返回 `b""`）—— 与裸 Python 的行为一致：
    `bytes(b)` 会在同一个位置抛同样的异常，守卫对线缆没有任何改变。
    旧写法把这类异常落到钩子的 `except Exception` → `_note_hook_error` →
    会话收尾谎报"守卫可能已静默失效"（见 §0c ③）。
    """
    parts = []
    for b in (buffers or []):
        p = _as_scannable_payload(b)
        if p is None:
            return b""          # 有扫不了的一块 → 整条不判（不假装扫过）
        parts.append(p)
    return b"".join(parts)


def _guard_send(sock, data, *a, **kw):
    try:
        payload = _as_scannable_payload(data)
        if payload is not None:
            _GUARD.scan_payload(sock, payload)
    except EgressBlocked:
        raise
    except Exception as exc:
        _GUARD._note_hook_error("send/sendall/sendmsg/sendto 钩子", exc)
    return _GUARD._orig_send(sock, data, *a, **kw)


def _guard_sendall(sock, data, *a, **kw):
    try:
        payload = _as_scannable_payload(data)     # r10 C1：不得退回类型白名单
        if payload is not None:
            _GUARD.scan_payload(sock, payload)
    except EgressBlocked:
        raise
    except Exception as exc:
        _GUARD._note_hook_error("send/sendall/sendmsg/sendto 钩子", exc)
    return _GUARD._orig_sendall(sock, data, *a, **kw)


def _guard_sendmsg(sock, buffers, *a, **kw):
    """C2：`sendmsg` 走的是 `buffers` 列表（r3 完全没钩 → 绕过）。"""
    try:
        data = _join_scannable_buffers(buffers)   # r11 I-1：扫不了就放行，不记"守卫异常"
        if data:
            _GUARD.scan_payload(sock, data)
    except EgressBlocked:
        raise
    except Exception as exc:
        _GUARD._note_hook_error("send/sendall/sendmsg/sendto 钩子", exc)
    return _GUARD._orig_sendmsg(sock, buffers, *a, **kw)


def _guard_os_write(fd, data):
    """C2：`os.write(fd, …)` 直写 socket（r3 完全没钩 → 绕过）。

    **只**判"这个 fd 真的是 socket"的写 —— 用 `fstat` 的 `S_ISSOCK` 做**权威**判定，
    不靠"曾经登记过这个 fd"（fd 会被文件复用：实测踩到 —— 收集期 pytest 写
    `.pyc` 时恰好复用了一个已关闭 socket 的 fd，而那个 .pyc 里就有
    `CONNECT api.deepseek.com:443` 字面量 → 误判成网络写 → 直接打断收集）。
    """
    try:
        payload = _as_scannable_payload(data)     # r10 C1：不得退回类型白名单
        if payload is not None and _GUARD._is_socket_fd(fd):
            _GUARD.scan_payload(None, payload, fd=fd)
    except EgressBlocked:
        raise
    except Exception as exc:
        _GUARD._note_hook_error("send/sendall/sendmsg/sendto 钩子", exc)
    return _GUARD._orig_os_write(fd, data)


def _peer_ip_of(sock):
    """socket 的**真实对端** IP（拿不到 → None；None 在判据里 = 判不了 = fail-closed）。"""
    try:
        peer = sock.getpeername()
    except Exception:
        return None
    if isinstance(peer, (tuple, list)) and peer:
        return peer[0]
    return None


def _tls_peer_acceptable(peer_ip) -> bool:
    """这次客户端 TLS 的**真实对端**是否可接受（r9 C1：SNI 不得单独作判据）。

    可接受 = ① 白名单主机解析出的 IP（**直连**：TLS 端到端到白名单目标），或
             ② **明文代理**主机解析出的 IP（HTTPS 走 `http://` 代理时，TLS 是端到端
                到目标、传输对端是代理；代理**之上**的目标由 D 层的 CONNECT 行判）。

    ⚠️ 两条"看起来能放宽"的路都**故意不给**：
    - **任意回环/私网 IP**：那正是 r9 C1 的绕过形态 —— 假终点/假代理在 `127.0.0.1`
      监听，配一个**撒谎的 SNI** 就能把明文送进去（本批实测复现：拆掉对端判据后
      `wrap_socket(…, server_hostname="open.bigmodel.cn")` 打 `127.0.0.1` → 守卫 0 反应，
      TLS 监听器**解密后**收到 `CONNECT api.deepseek.com:443`）。
    - **不加区分地认任何 IP**：等于把判据交回给 SNI（就是 r8 的缺陷本身）。

    残留（**已实测、已声明，未修**）：若测试进程自己把 `HTTPS_PROXY` 指到一个
    **能终结 TLS 的本地端点**，再配撒谎 SNI，对端会落进 ② 而被放行 —— 形态是
    "环境变量声称是明文代理、调用方却把它当不透明代理用"。本轮按控制方给的修法
    只做"SNI 必须真 + 对端必须可接受"的合取，该残留写进报告（未假装覆盖）。
    """
    if peer_ip is None:
        return False                    # 判不了对端 → fail-closed
    with _GUARD._lock:
        return str(peer_ip) in _GUARD._allowed_ips \
            or str(peer_ip) in _GUARD._proxy_ips


def _tls_target_refused(server_hostname, peer_ip):
    """客户端 TLS 的目标是否必须拒绝（**合取**判据；r9 C1 修法）。

    ## 为什么必须合取（r8 的回归）

    r8 为收窄误杀把判据改成"**只看调用方给的 `server_hostname`**"、**把 peer 检查整个删掉**
    ⇒ **SNI 可以撒谎**。审查者用**文档化的库参数**复现、本批也逐条复现（原始输出见
    `.superpowers/sdd/task-k61-r9-report.md`）：

    | 撒谎方式（全是公开/文档化参数） | r8 tip | 监听器（TLS 终结）看到的 |
    |---|---|---|
    | `httpx` `extensions={"sni_hostname": "open.bigmodel.cn"}` | 放行 | **解密后** `CONNECT api.deepseek.com:443` |
    | `urllib3.HTTPSConnectionPool(server_hostname="open.bigmodel.cn")` | 放行 | 同上 |
    | 裸 `ctx.wrap_socket(sock, server_hostname="open.bigmodel.cn")` | 放行 | 同上 |

    ## 判据（两条**同时**成立才放行）

    ① 有效 SNI 主机在白名单 —— 但 SNI 是**调用方自报**的，单用它 = 谁都能自称白名单；
    ② 真实对端可接受（见 `_tls_peer_acceptable`）—— 把"自称"钉回"实际连的是谁"。

    为什么②要**认代理 IP**（r7 的 peer 判据本来能挡住，但会误杀）：在配了 `http://`
    代理的机器上，客户端 TLS 的对端是**代理**而 SNI 是**目标** —— 不认代理 IP 就会把
    白名单主机的 TLS 误杀（r8 当初就是为这个才拆掉 peer 检查的）。r9 实测证明
    "认代理 IP"之后**误杀不会回来**：白名单 + （白名单学到的 IP ∪ 明文代理 IP）
    两条路都真跑通过（见报告 §r9-C1 的实测表）。

    加密不是豁免理由：**非白名单目标的 TLS 一律拒**；`server_hostname` 缺失 → 拒。
    """
    if not server_hostname or not _is_allowed_host(server_hostname):
        return True
    return not _tls_peer_acceptable(peer_ip)


def _tls_target_refused_sni_only(server_hostname):
    """**降级判据**（只判 SNI，**可被撒谎绕过**）—— wrap_bio / `SSLObject._create` 专用。

    ⚠️ 这是**降级声明**，不是覆盖：`wrap_bio` 拿到的是 `MemoryBIO`，**没有对端可判**
    （BIO 与 socket 之间没有反向引用，asyncio/anyio 的传输层也不把它递进来），
    所以合取判据的②在这里**做不到** —— 只能判 SNI。诚实结论：
    **对异步家族（asyncio `sslproto` / aiohttp / anyio）撒谎的 SNI 挡不住**，
    该路径为"降级：只判 SNI"。
    （为什么**不能**改成"异步一律 fail-closed"：那会把所有走 asyncio 的 HTTPS ——
      包括免费 GLM 的正规链路 —— 全部打死，是**误杀**而不是防护。）

    本函数在报告里对应的条目是"**已声明：可被撒谎绕过**"，不得计为已覆盖路径。
    """
    return not (server_hostname and _is_allowed_host(server_hostname))


def _guard_wrap_socket(self, sock, *a, **kw):
    """r7/r8/r9（C1）：**客户端 TLS** → fail-closed（合取判据见 `_tls_target_refused`）。"""
    server_side = _create_arg(a, kw, 0, "server_side", False)     # a[0] = server_side
    if not server_side:
        host = _create_arg(a, kw, 3, "server_hostname")           # a[3] = server_hostname
        peer_ip = _peer_ip_of(sock)
        if _tls_target_refused(host, peer_ip):
            _GUARD._trip(
                "ssl.SSLContext.wrap_socket（客户端 TLS）",
                str(host or peer_ip),
                extra=f"  server_hostname={host!r} 对端={peer_ip}\n"
                      "  判据（r9 合取）: ① SNI 必须是白名单主机（SNI 是调用方自报的，"
                      "**单独用会被撒谎绕过**）**且** ② 真实对端必须是白名单主机学到的 IP "
                      "或明文代理学到的 IP。\n"
                      "  与代理是怎么配置的无关（环境变量 / proxies= / mounts= / proxy= 都一样）。\n",
                exc=PublicEgressBlocked,
                rule="测试进程只允许对**白名单主机**发起客户端 TLS，且对端必须真的是那个"
                     "白名单目标（或明文代理）。非白名单目标上的 TLS 会藏住真实目标 → 拒绝。"
                     "确需明文可判的代理请用 `http://` 代理（CONNECT 行可见）。")
    # ⚠️ 挂成 **类属性** → 描述符协议生效，调用时第一个参数是 `self`（SSLContext）。
    # r7 实测踩到：写成 `(sock, *a, **kw)` 会把 SSLContext 当成 socket →
    # `getpeername()` 抛异常 → 被 except 吞掉 → **钩子形同虚设**（违规计数恒 0）。
    return _GUARD._orig_wrap_socket(self, sock, *a, **kw)


def _guard_wrap_bio(self, incoming, outgoing, *a, **kw):
    """r8（C1 主修）+ r9 降级声明：**异步家族**的客户端 TLS 入口（asyncio `sslproto` / aiohttp）。

    判据 = `_tls_target_refused_sni_only`（**只判 SNI**）：参数是 `MemoryBIO`，没有对端
    可判 → 合取判据的②**做不到** → 本路径**降级**、**可被撒谎的 SNI 绕过**（已实测并声明）。
    """
    server_side = _create_arg(a, kw, 0, "server_side", False)     # a[0] = server_side
    if not server_side:
        host = _create_arg(a, kw, 1, "server_hostname")           # a[1] = server_hostname
        if _tls_target_refused_sni_only(host):
            _GUARD._trip(
                "ssl.SSLContext.wrap_bio（客户端 TLS / 异步家族）",
                str(host or "<无 server_hostname>"),
                extra=f"  server_hostname={host!r}\n"
                      "  判据: 异步家族（asyncio sslproto / aiohttp）用 wrap_bio —— "
                      "r7 只钩了 wrap_socket，实测 `aiohttp` 显式 proxy= 时经此路漏出 CONNECT。\n"
                      "  ⚠️ 本路径**只判 SNI**（拿不到对端）→ **撒谎的 SNI 挡不住**，"
                      "属**降级声明**，不计为已覆盖。\n",
                exc=PublicEgressBlocked,
                rule="异步家族的客户端 TLS 同样只允许白名单主机；"
                     "`server_hostname` 为空时 fail-closed（判不了就拒）。")
    return _GUARD._orig_wrap_bio(self, incoming, outgoing, *a, **kw)


# ── r9 C2：TLS 入口的**同族**面 ──
# r8 只钩了 `SSLContext.wrap_socket` / `wrap_bio` 两个"友好入口"。实测（本批原始输出见报告）
# 同族里还有 **4 个**能建出客户端 TLS 的入口被整个漏掉：
#   `ssl.SSLSocket._create`（Python 类方法，**可钩**）、`ssl.SSLObject._create`（同上）、
#   `_ssl._SSLContext._wrap_socket` / `_wrap_bio`（C 方法，**不可直接赋值**，
#   但 `ssl.SSLContext` 是它的 Python 子类 → 在子类上挂同名属性即可**遮蔽**它）。
# 实测 r8 tip：`ssl.SSLSocket._create(sock=…, server_hostname="127.0.0.1")`、裸
# `SSLObject._create(server_hostname="api.deepseek.com")`、`ctx._wrap_socket(sock, False,
# "open.bigmodel.cn")` 三条**全部 0 反应**（监听器解密后收到 CONNECT）。

def _create_arg(a, kw, index, name, default=None):
    """`_create` 家族按"位置或关键字"取参数（它们既被位置调用、也被关键字调用）。"""
    if name in kw:
        return kw[name]
    return a[index] if len(a) > index else default


def _guard_sslsocket_create(cls, *a, **kw):
    """r9 C2：`ssl.SSLSocket._create` —— 客户端 TLS 的**同族入口**（合取判据）。"""
    sock = _create_arg(a, kw, 0, "sock")
    server_side = _create_arg(a, kw, 1, "server_side", False)
    host = _create_arg(a, kw, 4, "server_hostname")
    if not server_side:
        peer_ip = _peer_ip_of(sock) if sock is not None else None
        if _tls_target_refused(host, peer_ip):
            _GUARD._trip(
                "ssl.SSLSocket._create（客户端 TLS）",
                str(host or peer_ip),
                extra=f"  server_hostname={host!r} 对端={peer_ip}\n"
                      "  判据同 wrap_socket（合取）：SNI 必须在白名单**且**对端可接受。\n"
                      "  为什么要有这一层: r8 只钩 `SSLContext.wrap_socket/wrap_bio`，"
                      "`SSLSocket._create(sock=…, server_hostname=…)` 直调实测 **0 反应**。\n",
                exc=PublicEgressBlocked,
                rule="`ssl.SSLSocket._create` 是客户端 TLS 的同族入口，判据与 wrap_socket 相同。")
    return _GUARD._orig_sslsocket_create.__func__(cls, *a, **kw)


def _guard_sslobject_create(cls, *a, **kw):
    """r9 C2：`ssl.SSLObject._create` —— 同上，但**只判 SNI**（降级，无对端可判）。"""
    server_side = _create_arg(a, kw, 2, "server_side", False)
    host = _create_arg(a, kw, 3, "server_hostname")
    if not server_side:
        if _tls_target_refused_sni_only(host):
            _GUARD._trip(
                "ssl.SSLObject._create（客户端 TLS / BIO）",
                str(host or "<无 server_hostname>"),
                extra=f"  server_hostname={host!r}\n"
                      "  判据: 只有 BIO、**没有对端可判** → 降级为只判 SNI"
                      "（**撒谎的 SNI 挡不住**，见 `_tls_target_refused_sni_only`）。\n",
                exc=PublicEgressBlocked,
                rule="`ssl.SSLObject._create` 只接受白名单 SNI；该路径无对端可判 → 降级。")
    return _GUARD._orig_sslobject_create.__func__(cls, *a, **kw)


def _guard_ctx_wrap_socket(self, sock, *a, **kw):
    """r9 C2：`_ssl._SSLContext._wrap_socket` 的**遮蔽层**（挂在 Python 子类 `ssl.SSLContext` 上）。

    C 方法本身不可赋值（仍是声明的残留：`_ssl._SSLContext._wrap_socket(ctx, …)`
    这种**未绑定直调**绕过本遮蔽层），但**一切经 `ssl.SSLContext` 实例的调用**
    （含 `SSLSocket._create` 内部那次）都走这里 → 判据与 `wrap_socket` 同（合取）。
    """
    server_side = _create_arg(a, kw, 0, "server_side", False)
    host = _create_arg(a, kw, 1, "server_hostname")
    if not server_side:
        peer_ip = _peer_ip_of(sock) if sock is not None else None
        if _tls_target_refused(host, peer_ip):
            _GUARD._trip(
                "ssl.SSLContext._wrap_socket（C 层入口的遮蔽层）",
                str(host or peer_ip),
                extra=f"  server_hostname={host!r} 对端={peer_ip}\n"
                      "  判据同 wrap_socket（合取）。\n",
                exc=PublicEgressBlocked,
                rule="客户端 `_wrap_socket` 只允许白名单 SNI + 可接受对端。")
    return _GUARD._orig_ctx_wrap_socket(self, sock, *a, **kw)


def _guard_ctx_wrap_bio(self, incoming, outgoing, *a, **kw):
    """r9 C2：`_ssl._SSLContext._wrap_bio` 的**遮蔽层**（同上；只判 SNI = 降级）。"""
    server_side = _create_arg(a, kw, 0, "server_side", False)
    host = _create_arg(a, kw, 1, "server_hostname")
    if not server_side:
        if _tls_target_refused_sni_only(host):
            _GUARD._trip(
                "ssl.SSLContext._wrap_bio（C 层入口的遮蔽层 / 降级）",
                str(host or "<无 server_hostname>"),
                extra=f"  server_hostname={host!r}\n"
                      "  判据: 只有 BIO、无对端可判 → 降级为只判 SNI。\n",
                exc=PublicEgressBlocked,
                rule="客户端 `_wrap_bio` 只接受白名单 SNI；无对端可判 → 降级。")
    return _GUARD._orig_ctx_wrap_bio(self, incoming, outgoing, *a, **kw)


def _guard_sendto(sock, data, *a, **kw):
    """r7（C2 残余）：`sendto(data, addr)` —— r6 未挂钩，可把 CONNECT 整行送出。"""
    try:
        payload = _as_scannable_payload(data)     # r10 C1：不得退回类型白名单
        if payload is not None:
            _GUARD.scan_payload(sock, payload)
    except EgressBlocked:
        raise
    except Exception as exc:
        _GUARD._note_hook_error("send/sendall/sendmsg/sendto 钩子", exc)
    return _GUARD._orig_sendto(sock, data, *a, **kw)


def _guard_os_writev(fd, buffers, *a, **kw):
    """r7（C2 残余）：`os.writev(fd, buffers)` —— r6 未挂钩。"""
    try:
        if _GUARD._is_socket_fd(fd):
            data = _join_scannable_buffers(buffers)   # r11 I-1：扫不了就放行
            if data:
                _GUARD.scan_payload(None, data, fd=fd)
    except EgressBlocked:
        raise
    except Exception as exc:
        _GUARD._note_hook_error("send/sendall/sendmsg/sendto 钩子", exc)
    return _GUARD._orig_os_writev(fd, buffers, *a, **kw)


def _guard_os_sendfile(*a, **kw):
    """r8（C2）：`os.sendfile(out_fd, in_fd, …)` 把**文件内容**灌进 socket。

    内容判不了（要读文件、还可能边读边发）→ **fail-closed**。
    r7 曾把 `sendfile` 列为"豁免"，理由是"另有文件写守卫" —— **那个守卫不存在**：
    **理由造假比漏钩更坏**（后人会以为已论证过）。现收回豁免，改为拒。
    豁免理由若站不住就必须收回，这是本条的处置。
    """
    out_fd = a[0] if a else None
    if out_fd is not None and _GUARD._is_socket_fd(out_fd):
        _GUARD._trip("os.sendfile（socket ← file）", f"fd={out_fd}",
                     extra="  判据: 发的是文件内容，守卫无法判定其中是否含请求头明文 → fail-closed。\n",
                     exc=PublicEgressBlocked,
                     rule="测试进程不得用 sendfile 往 socket 灌文件内容（内容不可判）。"
                          "确需发送可判明文请用 send/sendall/os.write。")
    return _GUARD._orig_os_sendfile(*a, **kw)


def _guard_sock_sendfile(self, file, *a, **kw):
    """r8（C2）：`socket.socket.sendfile(file, …)` 同上 → fail-closed。"""
    _GUARD._trip("socket.socket.sendfile（socket ← file）", "sendfile",
                 extra="  判据: 发的是文件内容，守卫无法判定其中是否含请求头明文 → fail-closed。\n",
                 exc=PublicEgressBlocked,
                 rule="测试进程不得用 sendfile 往 socket 灌文件内容（内容不可判）。")
    return _GUARD._orig_sock_sendfile(self, file, *a, **kw)


# ── r9 C2 ③：`os.splice` —— file→pipe→socket 同族（r8 的派生谓词漏了它）──
#
# r8 的"发送族 API 面"是**用前缀猜出来的**：`name.startswith(("send", "write"))`。
# 那个谓词**天生不可靠**（本批实测）：`os.splice` 起头既不是 send 也不是 write，
# 却是能把**文件内容**灌进 socket 的通道 —— 实测 `os.splice(file→pipe)` +
# `os.splice(pipe→socket)` 把 `CONNECT api.deepseek.com:443` 原样送上监听器，守卫 0 反应。
# "按名字猜"还会有第二类错：名字像却**不是**通道（`socket.sendmsg_afalg` 名字像，
# 实测只对 AF_ALG 生效 → `OSError: algset is only supported for AF_ALG`）。
# ⇒ r9 起改为**显式枚举**（枚举来源与逐条处置见
#   `tests/test_k61_llm_egress_guard.py::TestSendFamilyApiSurface`），
#   前缀扫描退为"提醒人做决定"的**绊线**，不再充当判据。

def _guard_os_splice(src, dst, count, *a, **kw):
    """r9 C2：`os.splice(src, dst, …)` —— 内核里搬数据，内容**判不了** → 与 sendfile 同处置。

    只在 `dst` 真的是 socket fd 时 fail-closed（`src` 是 socket = 从网络**读**，不是出站）。
    """
    try:
        if _GUARD._is_socket_fd(dst):
            _GUARD._trip("os.splice（socket ← file/pipe）", f"src_fd={src} dst_fd={dst}",
                         extra="  判据: 内容在内核里从文件/管道搬到 socket，守卫判定不了"
                               "其中是否含请求头明文 → fail-closed。\n"
                               "  为什么 r8 漏了它: 派生谓词按 `send`/`write` 前缀猜名字，"
                               "`splice` 两头都不沾（实测 0 反应、明文原样出线）。\n",
                         exc=PublicEgressBlocked,
                         rule="测试进程不得用 splice 把文件/管道内容灌进 socket（内容不可判）。"
                              "确需发送可判明文请用 send/sendall/os.write。")
    except EgressBlocked:
        raise
    except Exception as exc:
        _GUARD._note_hook_error("os.splice 钩子", exc)
    return _GUARD._orig_os_splice(src, dst, count, *a, **kw)


def _guard_eventfd_write(fd, value):
    """r9：`os.eventfd_write(fd, value)` —— **把"值"写进 fd**（同族，本批实测新发现）。

    实测（本批原始输出见报告）：在一个 **socket fd** 上 `os.eventfd_write(fd, 0x204E4F43…)`
    → 监听器**真的**收到那 8 字节（`b'CONNECT '` = 该 uint64 的 little-endian）。
    也就是说：这是个**一次 8 字节**的写通道（任意 8 字节都可通过选值得到，
    除 `0` / `0xFFFFFFFFFFFFFFFF` 被拒）。
    判据处置：8 字节窗口里**不可能**出现请求头形状（`_REQUEST_HEAD_RE` 最短匹配
    `"Host:"` 也要 5 字节且需要后续行结构）→ 扫描没有意义 → 与 sendfile 同族 **fail-closed**。
    只在 `fd` 真的是 socket 时触发（正当用法写的是 eventfd，不是 socket）。
    """
    try:
        if _GUARD._is_socket_fd(fd):
            _GUARD._trip("os.eventfd_write（socket ← 计数器值）", f"fd={fd}",
                         extra="  判据: 每次只能塞 8 字节（值是 uint64，little-endian 落地），"
                               "判定不了是否在拼请求头明文 → fail-closed。\n"
                               "  为什么 r8 漏了它: 派生谓词只按 `send`/`write` 前缀找名字，"
                               "`eventfd_write` 的写语义藏在后缀里。\n",
                         exc=PublicEgressBlocked,
                         rule="测试进程不得用 eventfd_write 往 socket 里塞值（内容不可判）。"
                              "确需发送可判明文请用 send/sendall/os.write。")
    except EgressBlocked:
        raise
    except Exception as exc:
        _GUARD._note_hook_error("os.eventfd_write 钩子", exc)
    return _GUARD._orig_eventfd_write(fd, value)


# ── r9 I1：**把 socket fd 包成文件对象**的族（`io.FileIO` / `os.fdopen` / `io.open` /
#    `builtins.open` / `shutil.copyfileobj`）──
#
# 实测（本批原始输出见报告）：`io.FileIO(sock.fileno(), "wb").write(b"CONNECT api.deepseek.com:443 …")`
# / `os.fdopen(sock.fileno(), "wb").write(…)` / `shutil.copyfileobj(f, os.fdopen(sock.fileno(), "wb"))`
# 三条**全部 0 反应**，明文原样到达监听器。根因：`FileIO.write` 是 **C 层直写 fd**
# （走 `write(2)`），**不经过** `os.write` / `socket.send` 任何钩子 —— 与 `os.sendfile`
# 是同一个根因（"内容可见性"取决于走哪条路，而不是内容本身）。
# 处置（与 sendfile 同族）：**fail-closed**，且**只在这个 fd 真的是 socket 时触发**
# （`fstat` 的 `S_ISSOCK` 权威判定）→ 正常文件 I/O 一行不受影响。
#
# 为什么不用"扫描写入内容"（更宽松）替代 fail-closed：
# - `io.open`/`os.fdopen`/`builtins.open` 返回的是 C 的 `BufferedWriter`/`TextIOWrapper`，
#   内容在 `FileIO`（C）里落盘/落线 —— 要扫就得把**调用方拿到的对象**换成代理，
#   那会改掉整个进程里每个 `open()` 的类型/行为（产品码在跑，风险不可接受）；
# - `io.FileIO` 可以子类化扫描，但"构造函数拒一个 socket fd"与另外几条**同一处置**更好记、
#   也更符合本守卫既有的"内容不可判 → 拒"口径（见 sendfile / 不透明代理）。
# - `shutil.copyfileobj` 是**局部**的（接收端对象只在本函数内用）→ 那里用**扫描代理**
#   （内容可判，故不 fail-closed，零误杀）。

def _check_file_object_over_socket_fd(where: str, fd):
    """`fd` 真的是 socket → 拒（把 socket 包成文件对象 = 内容看不见）。"""
    if isinstance(fd, bool) or not isinstance(fd, int):
        return
    if not _GUARD._is_socket_fd(fd):
        return
    _GUARD._trip(where, f"fd={fd}",
                 extra="  判据: 文件对象的写入是 **C 层直写 fd**（`write(2)`），"
                       "绕过 `os.write` / `socket.send` 全部钩子 → 内容不可判 → fail-closed。\n"
                       "  r9 实测（r8 tip）: `io.FileIO` / `os.fdopen` / `shutil.copyfileobj` "
                       "三条公开 API 全部 0 反应、明文原样出线。\n",
                 exc=PublicEgressBlocked,
                 rule="测试进程不得把 socket fd 包成文件对象来写（内容不可判）。"
                      "确需发送可判明文请用 send/sendall/os.write/writev。")


class _GuardedFileIO(io.FileIO):
    """`io.FileIO` 的守卫替身：**构造期**判定 fd 是不是 socket（r9 I1）。"""

    def __init__(self, file, mode="r", closefd=True, opener=None):
        _check_file_object_over_socket_fd("io.FileIO（socket fd ← 文件对象）", file)
        super().__init__(file, mode, closefd, opener)


def _socket_fd_of_fileobj(obj):
    """对象背后是不是 socket fd（是 → 返回 fd；否/拿不到 → None）。"""
    try:
        fd = obj.fileno()
    except Exception:
        return None
    if isinstance(fd, bool) or not isinstance(fd, int):
        return None
    return fd if _GUARD._is_socket_fd(fd) else None


class _ScanningWriteProxy:
    """`shutil.copyfileobj` 专用的**临时**接收端代理：写一块 → 判一块 → 再写。

    只在 `fdst` 真的是 socket 支撑时才包（其他情况一行不变）；`copyfileobj` 只用到
    `.write`，其余属性一律转发给真对象。
    """

    __slots__ = ("_fdst", "_fd")

    def __init__(self, fdst, fd):
        self._fdst = fdst
        self._fd = fd

    def write(self, data):
        payload = _as_scannable_payload(data)     # r10 C1：不得退回类型白名单
        if payload is not None:
            _GUARD.scan_payload(None, payload, fd=self._fd)
        return self._fdst.write(data)

    def __getattr__(self, name):        # 只在正常查找失败时触发（slots 下无 __dict__）
        return getattr(self._fdst, name)


def _guard_fdopen(fd, *a, **kw):
    """r9 I1：`os.fdopen(fd, …)` —— fd 是 socket → 拒。"""
    _check_file_object_over_socket_fd("os.fdopen（socket fd ← 文件对象）", fd)
    return _GUARD._orig_fdopen(fd, *a, **kw)


def _guard_io_open(file, *a, **kw):
    """r9 I1：`io.open` / **`builtins.open`** —— 传的是 **fd** 且是 socket → 拒。

    为什么连 `builtins.open` 一起换：`io.open is builtins.open` 为 True，但它们是
    **两个绑定**（模块属性 vs 内建命名空间），只补一处时 `open(sock_fd, "wb")`
    仍能绕（r8 的"别名"教训的同型）。
    只对 `int` 的首参做判定 → 路径/`PathLike` 直接透传，正常 `open()` 零行为差异。
    """
    _check_file_object_over_socket_fd("io.open / builtins.open（socket fd ← 文件对象）", file)
    return _GUARD._orig_io_open(file, *a, **kw)


def _guard_copyfileobj(fsrc, fdst, length=0):
    """r9 I1：`shutil.copyfileobj(src, dst)` —— `dst` 是 socket 支撑 → **逐块扫内容**。

    这里**不**fail-closed：内容是可见的（逐块经过本函数），所以按既有 D 层口径判
    （只有真的像请求头且目标是 deepseek/非白名单时才拦）→ 本地 socket 的正常拷贝不误杀。
    """
    fd = _socket_fd_of_fileobj(fdst)
    if fd is None:
        return _GUARD._orig_copyfileobj(fsrc, fdst, length)
    return _GUARD._orig_copyfileobj(fsrc, _ScanningWriteProxy(fdst, fd), length)


def _guard_httpx_sync(transport, request):
    host = request.url.host
    if _is_blocked(host):
        _GUARD._trip("httpx.HTTPTransport.handle_request", host,
                     f"  请求: {request.method} {request.url}\n")
    return _GUARD._orig_httpx_sync(transport, request)


async def _guard_httpx_async(transport, request):
    host = request.url.host
    if _is_blocked(host):
        _GUARD._trip("httpx.AsyncHTTPTransport.handle_async_request", host,
                     f"  请求: {request.method} {request.url}\n")
    return await _GUARD._orig_httpx_async(transport, request)


#: r3：**模块级安装**（不只靠 session 夹具）—— 夹具在**收集/导入之后**才跑，
#: 而收集期就有出站（实测 `web_search_available()` 会在 import 时探一次搜索通道）。
#: 模块级安装让守卫从 conftest 被加载的那一刻起就生效；session 夹具只负责
#: 卸载与违规汇报。
_GUARD.install()

# r7：sqlite3.connect 路径钩子（与 fd 看门狗同一个判据族：只看**本会话**）
try:
    import sqlite3 as _sqlite3
    if not getattr(_sqlite3.connect, "_k61_wrapped", False):
        _sqlite3_orig_connect = _sqlite3.connect
        _sqlite3.connect = _guard_sqlite_connect(_sqlite3_orig_connect)
        try:
            _sqlite3.connect._k61_wrapped = True
        except Exception:
            pass
except Exception:      # pragma: no cover
    pass


@pytest.fixture(scope="session", autouse=True)
def _k61_deepseek_egress_guard():
    """进程级守卫：测试期间对 deepseek 域的真实出站一律失败（全量套件自然触发）。"""
    try:
        yield _GUARD
    finally:
        _GUARD.uninstall()
        hook_errors = list(_GUARD.hook_errors)
        _GUARD.hook_errors.clear()
        violations = list(_GUARD.violations)
        _GUARD.violations.clear()
        if hook_errors:
            # r8：钩子自己出错 = **守卫可能已静默失效** → 必须响亮（r7 教训）
            raise AssertionError(
                f"[k61 守卫] {len(hook_errors)} 个钩子自身抛异常（守卫可能已静默失效）：\n  "
                + "\n  ".join(hook_errors))
        if violations:
            # 双保险：即使被测代码把 DeepSeekEgressBlocked 也吞了，这里仍然报红。
            raise AssertionError(
                f"[k61 守卫] 本会话共 {len(violations)} 次 DeepSeek 出站尝试：\n"
                + "\n".join(violations))


# ══════════════════════════════════════════════════════════════════
# 2) 免费 GLM 路由（真实 LLM 端到端用例专用）
# ══════════════════════════════════════════════════════════════════

def _glm_route_adapter(api_key, messages, model="deepseek-flash", max_tokens=1000,
                       temperature=0.7, timeout=60.0, client=None,
                       stream_cb=None, tools=None, tool_choice=None):
    """`deepseek_anthropic_completion` 的同签名替身 → 免费 glm-4-flash。

    与 `scripts/verify_qa_scenarios.py::_routed` / `scripts/eval_agent/l1_eval.py::_routed`
    同款：调用方传进来的 `api_key`/`model` 一律丢弃（它们来自 .env，是付费
    生产档），强制换成环境变量里的 `ZHIPU_API_KEY` + `FREE_LLM_MODEL`。
    `tools`/`tool_choice` 为 Anthropic 原生 tool_use 链参数，OpenAI 兼容端点
    不适用 → 剥掉（与本仓库 `provider=glm` 的既有语义一致）。
    """
    import src.llm.client as llm_client
    glm_key = os.environ.get("ZHIPU_API_KEY", "").strip()
    if not glm_key:
        raise RuntimeError("glm_route 需要 ZHIPU_API_KEY（夹具应在无 key 时 skip）")
    return llm_client.glm_openai_completion(
        glm_key, messages, model=FREE_LLM_MODEL,
        max_tokens=max_tokens, temperature=temperature, timeout=timeout,
        client=client, stream_cb=stream_cb)


@pytest.fixture
def glm_api_key():
    """免费 GLM key（无则 skip）。"""
    key = os.environ.get("ZHIPU_API_KEY", "").strip()
    if not key:
        pytest.skip("缺少 ZHIPU_API_KEY（免费 glm-4-flash 路由）")
    return key


@pytest.fixture
def glm_route(monkeypatch, glm_api_key):
    """把统一 LLM 层接到**免费 glm-4-flash**，供真实端到端用例使用。

    用法：`def test_x(glm_route): ... advisor.generate(..., api_key=glm_route)`。
    返回 GLM key（引擎要求 api_key 非空才走 LLM 路径）。
    """
    import src.llm.client as llm_client
    monkeypatch.setattr(llm_client, "deepseek_anthropic_completion",
                        _glm_route_adapter)
    return glm_api_key


@pytest.fixture
def mock_deepseek_http(monkeypatch):
    """固定 DeepSeek 端点的**请求形状**，零外呼（k61：形状断言专用）。

    用 `httpx.MockTransport` 接管传输层：统一层的 payload/headers 构造与响应
    解析**全程照跑**（不是 patch 掉统一层函数），只是字节不出网。`MockTransport`
    是独立类，不经过进程守卫拦的 `HTTPTransport` → 形状断言与守卫互不干扰。

    用法：
        captured = mock_deepseek_http('{"actions": [...]}')
        ...被测代码...
        assert captured[0]["json"]["model"] == "deepseek-flash[1m]"

    返回 `captured` 列表（每项 `{method, url, headers, json}`，headers 键小写）。
    """

    def _install(response_text: str = "{}", status_code: int = 200):
        import httpx

        captured = []

        def handler(request: "httpx.Request") -> "httpx.Response":
            import json as _json
            try:
                body = _json.loads(request.content.decode("utf-8"))
            except Exception:
                body = None
            captured.append({
                "method": request.method,
                "url": str(request.url),
                "headers": {k.lower(): v for k, v in request.headers.items()},
                "json": body,
            })
            return httpx.Response(
                status_code,
                json={"content": [{"type": "text", "text": response_text}],
                      "stop_reason": "end_turn"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(httpx, "post", client.post)
        return captured

    return _install


# ══════════════════════════════════════════════════════════════════
# 3) 标记注册
# ══════════════════════════════════════════════════════════════════

def pytest_configure(config):
    """注册 `e2e` 标记（真实 LLM 端到端冒烟，**默认 skip**）。

    触发方式（两者可合用）：
        K61_E2E=1 pytest tests/test_k61_e2e_smoke.py -q          # 开关触发
        K61_E2E=1 pytest tests/ -m e2e -q                        # 按标记筛选
    注册标记是为了让 `-m e2e` 不产生 PytestUnknownMarkWarning，
    并让 `pytest --markers` 能自解释这条冒烟的用途与代价。
    """
    config.addinivalue_line(
        "markers",
        "e2e: 真实 LLM 端到端冒烟（免费 glm-4-flash）；默认 skip，"
        "需 K61_E2E=1 触发。会真发网络请求，不进常规门禁。",
    )
