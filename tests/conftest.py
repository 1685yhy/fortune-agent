# -*- coding: utf-8 -*-
"""k61：测试进程级 LLM 出站守卫 + 免费 GLM 路由夹具（本批核心交付）。

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

## 本文件提供两条防线

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

2. `glm_route` fixture——需要**真实 LLM** 的端到端用例走**免费 `glm-4-flash`**。
   做法与仓库既有评测路由完全同款（`scripts/verify_qa_scenarios.py`、
   `scripts/eval_agent/l1_eval.py` 的 `_routed`）：把统一 LLM 层的
   `src.llm.client.deepseek_anthropic_completion` 接到
   `src.llm.client.glm_openai_completion`（model 固定 `glm-4-flash`）。
   无 `ZHIPU_API_KEY` 时 skip（与 `tests/test_eval_l4.py::_llm_keys_ready` 同约定）。
"""
import os
import socket

import pytest


# ══════════════════════════════════════════════════════════════════
# 0) 常量与异常
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
# 1) 免费 GLM 路由（真实 LLM 端到端用例专用）
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
