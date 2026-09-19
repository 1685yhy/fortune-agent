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
import shutil
import socket
import subprocess
import tempfile

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
    "FAISS_INDEX_DIR": "生产索引目录；pin 空 = 用代码默认（实测与部署值同值，零行为差异）",
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


_pin_test_env()

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


class RepoDirtDetected(BaseException):
    """测试会话把**被 git 跟踪**的文件写脏了（r2 ④）。

    同样继承 `BaseException`：不得被被测代码的 `except Exception` 吞掉。
    """


#: 会话起点的工作区状态（conftest **导入期**快照 —— 比收集期更早，连
#: 「测试模块在 import 时写脏文件」也能覆盖）。守卫据此做增量比对；
#: 锁用例 `tests/test_k61_repo_hygiene.py` 也读它。
SESSION_START_DIRTY = _tracked_dirty()


@pytest.fixture(scope="session", autouse=True)
def _k61_repo_dirt_guard():
    """会话级防复发锁：会话期间**新弄脏**的被跟踪文件 → 会话收尾报红。"""
    before = SESSION_START_DIRTY
    try:
        yield
    finally:
        newly = _tracked_dirty() - before
        if newly:
            raise RepoDirtDetected(
                "[k61 仓库卫生守卫] 测试会话写脏了被 git 跟踪的文件：\n"
                + "\n".join(f"  M {p}" for p in sorted(newly))
                + "\n处置：让写方落到 tmp 目录（UserMemory 用 USER_MEMORY_DIR / "
                  "base_dir；run_comparison 用 out_path 参数），不要往仓库内写。"
            )


# ══════════════════════════════════════════════════════════════════
# 1) 常量与异常
# ══════════════════════════════════════════════════════════════════

#: 禁止出站的域（子串匹配，大小写不敏感）——深寻是**付费生产**模型。
BLOCKED_HOST_MARKERS = ("deepseek",)

#: 免费 LLM 源白名单（守卫放行 + 正向对照基准）。只放免费源。
FREE_LLM_HOSTS = ("open.bigmodel.cn",)

#: `glm_route` 夹具强制使用的免费模型。
FREE_LLM_MODEL = "glm-4-flash"


class DeepSeekEgressBlocked(BaseException):
    """测试进程内对 deepseek 域发起真实出站连接。

    故意继承 `BaseException`：被测代码的 `except Exception` 兜底降级
    （advisor_v2 / mood_detector / jian_quote / night_soliloquy 均有）
    不得把这条守卫失败吞掉，否则守卫形同虚设。
    """


def _is_blocked(host) -> bool:
    h = str(host or "").lower()
    return any(m in h for m in BLOCKED_HOST_MARKERS)


class _EgressGuard:
    """安装/卸载出站守卫，并记录违规（即使异常被吞也能在 session 收尾报红）。"""

    def __init__(self):
        self.violations = []
        self._orig_getaddrinfo = None
        self._httpx = None

    # ── 记录 ──
    def _trip(self, where: str, host: str, extra: str = ""):
        detail = (f"[k61 守卫] 测试进程尝试对深寻主机发起真实出站请求 → 已拦截。\n"
                  f"  拦截层: {where}\n  主机: {host}\n{extra}"
                  f"  规则: 测试一律不得打 DeepSeek（付费/生产）；"
                  f"需要真实 LLM 请用 `glm_route` 夹具走免费 {FREE_LLM_MODEL}，"
                  f"只需验证形状请用 httpx.MockTransport。\n"
                  f"  免费源白名单: {', '.join(FREE_LLM_HOSTS)}")
        self.violations.append(detail)
        raise DeepSeekEgressBlocked(detail)

    # ── 安装 ──
    def install(self):
        self._orig_getaddrinfo = socket.getaddrinfo
        self._orig_create_connection = socket.create_connection
        socket.getaddrinfo = _guard_getaddrinfo
        socket.create_connection = _guard_create_connection
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
        if getattr(self, "_httpx", None) is not None:
            self._httpx.HTTPTransport.handle_request = self._orig_httpx_sync
            self._httpx.AsyncHTTPTransport.handle_async_request = self._orig_httpx_async
        self._orig_getaddrinfo = None


_GUARD = _EgressGuard()


# ── 模块级替身：必须以「裸函数」形态挂到 socket / httpx 上 ──
# （把 bound method 赋成类属性时描述符协议不生效，调用方少传一个 self →
#  TypeError；这些替身在被调用时才解析模块级 `_GUARD`，故顺序无碍。）
def _guard_getaddrinfo(host, *a, **kw):
    if _is_blocked(host):
        _GUARD._trip("socket.getaddrinfo", host)
    return _GUARD._orig_getaddrinfo(host, *a, **kw)


def _guard_create_connection(address, *a, **kw):
    host = address[0] if isinstance(address, (tuple, list)) and address else address
    if _is_blocked(host):
        _GUARD._trip("socket.create_connection", host)
    return _GUARD._orig_create_connection(address, *a, **kw)


def _guard_httpx_sync(transport, request):
    if _is_blocked(request.url.host):
        _GUARD._trip("httpx.HTTPTransport.handle_request", request.url.host,
                     f"  请求: {request.method} {request.url}\n")
    return _GUARD._orig_httpx_sync(transport, request)


async def _guard_httpx_async(transport, request):
    if _is_blocked(request.url.host):
        _GUARD._trip("httpx.AsyncHTTPTransport.handle_async_request", request.url.host,
                     f"  请求: {request.method} {request.url}\n")
    return await _GUARD._orig_httpx_async(transport, request)


@pytest.fixture(scope="session", autouse=True)
def _k61_deepseek_egress_guard():
    """进程级守卫：测试期间对 deepseek 域的真实出站一律失败（全量套件自然触发）。"""
    _GUARD.install()
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
