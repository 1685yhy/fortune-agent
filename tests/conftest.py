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
1. `_k61_deepseek_egress_guard`（autouse / session）——**进程级**拦截：
   测试期间任何指向 `deepseek` 域的真实出站连接一律以
   `DeepSeekEgressBlocked(BaseException)` 失败。拦截点在「真正会开 socket 的那一层」：
     - `socket.getaddrinfo`（覆盖 httpx/anyio/requests/urllib/原生 socket）
     - `socket.create_connection`（覆盖标准库同步路径）
     - `httpx.HTTPTransport.handle_request` / `httpx.AsyncHTTPTransport.handle_async_request`
       （**只有真实传输层**才命中；`httpx.MockTransport` 是独立类，不受影响 →
       用 mock transport 固定请求形状的用例照常工作）
   免费源白名单 `FREE_LLM_HOSTS`（只放免费源，当前 `open.bigmodel.cn`）不受影响；
   该白名单在 `tests/test_k61_llm_egress_guard.py` 里做正向对照（白名单必须放行）。

   为什么用 `BaseException` 子类：被测代码普遍写 `except Exception:` 做兜底降级
   （advisor/mood_detector/jian_quote 都是），用 `AssertionError` 会被**静默吞掉**，
   守卫就形同虚设。另配 `_VIOLATIONS` 记录 + session 收尾断言，双保险。

   ⚠️ 有了 §0 的 pin 之后，**这一层是兜底而不是唯一防线**：pin 让测试进程连
   DeepSeek key 都没有（结构性免疫），守卫负责拦住那些不依赖环境变量、
   用 Mock/硬编码 key 直接调统一层的调用点。

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
import os
import re
import shutil
import socket
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

#: 生产数据根（I4 守卫据此断言"跑测试不打开生产向量库"）。
PROD_DATA_ROOTS = ("/mnt/d/fortune-data", "/home/a/data")

#: 会话级临时目录：把 `UserMemory()` 的默认目录（repo 内 `data/memory/`）重定向出去。
#: 这是 `UserMemory` 自己文档化的隔离口（`USER_MEMORY_DIR`，「测试隔离用」）。
#: 不重定向时，任何走默认目录的用例都会写脏**被 git 跟踪**的 `data/memory/*.json`
#: （实测全量跑必现 `M data/memory/.json`）。
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
    os.environ["USER_MEMORY_DIR"] = TEST_MEMORY_DIR
    # I4：路径类 pin 到沙箱（不能 pin 空值 —— 空值会落到代码默认 = 生产路径）
    os.environ["VECTORDB_DIR"] = TEST_VECTORDB_DIR
    os.environ["FAISS_INDEX_DIR"] = TEST_FAISS_DIR
    os.environ["FORTUNE_DB_PATH"] = os.path.join(TEST_DB_DIR, "fortune.db")


_pin_test_env()

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
# 0b) 仓库卫生守卫（r2 / ④）：测试不得写脏**被 git 跟踪**的文件
# ══════════════════════════════════════════════════════════════════
#
# 实测（r2 基线，全量 `pytest tests/ -q`）：跑完必脏两个被跟踪文件 ——
#   M data/memory/.json                    （走 UserMemory 默认目录的用例）
#   M src/engine/out/comparison_runs.jsonl （run_comparison 的硬编码输出路径）
# 两者都已在 r2 修根因（见 §0 的 USER_MEMORY_DIR 重定向 + run_comparison 的
# `out_path` 参数）。本守卫是**防复发锁**：会话结束时比对「会话开始时干净、
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


class ProdDataTouched(BaseException):
    """测试会话碰了生产数据文件（r5 / I4）。同样 `BaseException`，吞不掉。"""


@pytest.fixture(scope="session", autouse=True)
def _k61_prod_data_guard():
    """会话级：生产数据文件（代码默认路径）的 mtime 在会话期间**不得变化**。"""
    before = _prod_data_mtimes()
    try:
        yield
    finally:
        after = _prod_data_mtimes()
        touched = [p for p in before if before[p] != after[p]]
        if touched:
            raise ProdDataTouched(
                "[k61 生产数据守卫] 测试会话碰了生产数据文件（mtime 变化）：\n"
                + "\n".join(f"  {p}" for p in touched)
                + "\n处置：让被测代码走沙箱目录（conftest 已 pin VECTORDB_DIR / "
                  "FORTUNE_DB_PATH / FAISS_INDEX_DIR 到测试目录），不要打开生产库。"
            )


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
        self._orig_getaddrinfo = None
        self._httpx = None
        self._lock = threading.Lock()
        #: 白名单主机解析出的 IP（会话内学习）——C 层据此放行合法目标。
        self._allowed_ips = set()
        #: 不透明代理主机解析出的 IP（C1：这些 IP:port 的连接一律 fail-closed）。
        self._opaque_proxy_ips = set()
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
        if _is_allowed_host(host) or str(host or "").lower() in _proxy_hosts():
            with self._lock:
                self._allowed_ips.add(str(ip))

    def _ip_allowed(self, ip) -> bool:
        if _is_non_public_ip(ip):
            return True
        with self._lock:
            return str(ip) in self._allowed_ips

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
        self._orig_os_write = os.write
        socket.getaddrinfo = _guard_getaddrinfo
        socket.create_connection = _guard_create_connection
        socket.socket.connect = _guard_connect
        socket.socket.connect_ex = _guard_connect_ex
        socket.socket.send = _guard_send
        socket.socket.sendall = _guard_sendall
        socket.socket.sendmsg = _guard_sendmsg
        os.write = _guard_os_write
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
        os.write = self._orig_os_write
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
            head = bytes(buf[:64]).lstrip()
            if not _REQUEST_HEAD_RE.match(head.decode("latin-1", "replace")):
                return
            text = bytes(buf).decode("latin-1", "replace")
        for line in text.split("\r\n")[:8]:
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


#: 「这次写是请求头起始」的判据（只按这个扫明文，避免扫 body 假红）。
_REQUEST_HEAD_RE = re.compile(
    r"^(CONNECT|GET|POST|PUT|DELETE|HEAD|PATCH|OPTIONS|TRACE)\s|^Host:",
    re.IGNORECASE,
)


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
    if low.startswith("connect "):
        target = s.split(None, 1)[1].split()[0]
        return _strip_port(target)
    if low.startswith("host:"):
        return _strip_port(s.split(":", 1)[1].strip())
    m = re.match(r"^[A-Za-z]{3,10}\s+https?://([^/\s]+)", s)
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


def _guard_send(sock, data, *a, **kw):
    try:
        if isinstance(data, (bytes, bytearray, memoryview)):
            _GUARD.scan_payload(sock, data)
    except EgressBlocked:
        raise
    except Exception:
        pass
    return _GUARD._orig_send(sock, data, *a, **kw)


def _guard_sendall(sock, data, *a, **kw):
    try:
        if isinstance(data, (bytes, bytearray, memoryview)):
            _GUARD.scan_payload(sock, data)
    except EgressBlocked:
        raise
    except Exception:
        pass
    return _GUARD._orig_sendall(sock, data, *a, **kw)


def _guard_sendmsg(sock, buffers, *a, **kw):
    """C2：`sendmsg` 走的是 `buffers` 列表（r3 完全没钩 → 绕过）。"""
    try:
        data = b"".join(bytes(b) for b in (buffers or []))
        if data:
            _GUARD.scan_payload(sock, data)
    except EgressBlocked:
        raise
    except Exception:
        pass
    return _GUARD._orig_sendmsg(sock, buffers, *a, **kw)


def _guard_os_write(fd, data):
    """C2：`os.write(fd, …)` 直写 socket（r3 完全没钩 → 绕过）。

    **只**判"这个 fd 真的是 socket"的写 —— 用 `fstat` 的 `S_ISSOCK` 做**权威**判定，
    不靠"曾经登记过这个 fd"（fd 会被文件复用：实测踩到 —— 收集期 pytest 写
    `.pyc` 时恰好复用了一个已关闭 socket 的 fd，而那个 .pyc 里就有
    `CONNECT api.deepseek.com:443` 字面量 → 误判成网络写 → 直接打断收集）。
    """
    try:
        if isinstance(data, (bytes, bytearray, memoryview)) and _GUARD._is_socket_fd(fd):
            _GUARD.scan_payload(None, data, fd=fd)
    except EgressBlocked:
        raise
    except Exception:
        pass
    return _GUARD._orig_os_write(fd, data)


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


@pytest.fixture(scope="session", autouse=True)
def _k61_deepseek_egress_guard():
    """进程级守卫：测试期间对 deepseek 域的真实出站一律失败（全量套件自然触发）。"""
    try:
        yield _GUARD
    finally:
        _GUARD.uninstall()
        violations = list(_GUARD.violations)
        _GUARD.violations.clear()
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
