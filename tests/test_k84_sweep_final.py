# -*- coding: utf-8 -*-
"""k84 回归门禁 —— **收尾批（把所有已知剩余项一次清掉）** 的后端面。

覆盖（控制方定规：「**只要是报的，都要修**」+「不要老是一步一步来」）：
  T1 **必修1（P1）未鉴权 HTML 路由审计**：把"全仓返回 HTML/文件的无鉴权路由"
     做成**可复跑判据**（AST 面 + 运行面），逐条钉住**判定**：
       · `① 死路由/孤儿页 + 全动作失效` → 删：`GET /scenarios`、`GET /compatibility`
         （判据沿用 k61-P4 删 `GET /membership` 的 f596995 那一条）；
       · `② 该加鉴权/该收口` → 收口：FastAPI 自带的 `/docs`、`/redoc`、
         `/openapi.json`（此前**从未被任何批次评估过**，默认开启且无鉴权）改为
         **fail-closed 默认关闭**，只认显式 `FORTUNE_ENABLE_DOCS=1`；
       · `③ 本就该公开` → **登记**并写明理由：`/`、`/pricing`、`/share`、
         `/share/{reading_id}`、`/api/chat/uploads/{filename}`、
         `/api/user/avatar/{user_id}`；
       · 已鉴权（登记备查）：`/report/{reading_id}`。
     **新增一条返回 HTML/文件的路由而不在本表表态 ⇒ 红。**
  T2 **必修7** `/api/share/{report_id}` 的**形态白名单**（k79 报出、当时未加）：
     与 `_READING_ID_RE` 同口径 + 新增 `_CONSULT_ID_RE`（ASCII 数字）；
     **并实测正常路径不受影响**（8 位 hex / 全数字 hex / 咨询 ID 三条逐个走通，
     与 k80-M5 的既有断言交叉印证）；顺带钉住"白名单拦下的怪串**不读盘、不查库**"。
  T3 **卫生**：被删的死资产/死页不得回来（`src/static/scenarios.html`、
     `src/chat.html`、`_build_compatibility_html`）。

红线：**不跑全量**、**不开生产库**（报告目录 monkeypatch 到 tmp、`_dao` 注入）、
不触网（LLM 层不参与）、不新增 skip/xfail、**未放宽任何既有断言**。
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

REPO = Path(__file__).resolve().parent.parent

# ══════════════════════════════════════════════════════════════════════
# T1 必修1：无鉴权 HTML / 文件响应面的**已登记清单**
# ══════════════════════════════════════════════════════════════════════

#: 判据（**可复跑**，不是人眼）：对 `src/**/*.py` 做 AST 扫描，取
#: 「GET 路由 + 函数体里出现 `HTMLResponse` / `FileResponse`」的 (文件, path)。
#: 另加 `app.mount(...)` 的 **StaticFiles 挂载**（本仓 1 处）。
#:
#: 每条的 `verdict` 是本批的**逐条判定**（① 删 / ② 加鉴权或收口 / ③ 登记公开），
#: `auth` 是**运行面实测**的鉴权状态（由 `_auth_of()` 从路由依赖链读出来，
#: 不是手写的），两者都不一致 ⇒ 红。
HTML_SURFACE = {
    "src/main.py::/": {
        "auth": "anon", "kind": "html", "verdict": "③",
        "why": "域名根着陆页（src/index.html）。无个人信息、无表单、无接口调用；"
               "k61-P4（f596995）已明确把它列为「同类核查：无同类死页」而保留。"
               "**k85 必修2 已修**：页内唯一动作原来是 `<a href=\"/chat\">` —— 指向一个"
               "**从来不存在**的路由（`git log -S'@app.get(\"/chat\")'` 零命中）＝坏链；"
               "控制方拍板改指 `/pricing`（公开、匿名可读、页内 `fetch('/api/pricing')`"
               "匿名可用 ⇒ 点进去有内容）。k85 已用原始 HTTP 实测该链接 200。",
    },
    "src/main.py::/api/charts/{filename}": {
        "auth": "auth", "kind": "file", "verdict": "②已具备",
        "why": "**k85 必修1 新增**的私有命盘图路由（`bazi_*` / `ziwei_*` / `fengshui_*`）。"
               "之所以必须存在：改前这些私有图与分享卡**混装**在同一个被匿名挂载的"
               "`CHARTS_DIR` 里（秒级时间戳 ⇒ 可枚举、无 TTL），控制方裁定收窄挂载面 ⇒ "
               "私有图改落 `private_charts_dir()` 并只经本路由下发。"
               "鉴权 = `Depends(require_user)` + **归属校验**"
               "（文件名带 `HMAC(secret, user_id)` 归属令牌，`chart_files.verify_owner`；"
               "**同一个**判据也被注销清理复用）。**只发 .png**：`.html` 是 Playwright 的"
               "中间产物（裸模板产物，改前被匿名以 text/html 下发 ⇒ 存储型 XSS 通路），"
               "本路由故意不发。",
    },
    "src/main.py::/pricing": {
        "auth": "anon", "kind": "html", "verdict": "③",
        "why": "定价与反诈声明 —— **公开信息**（其数据源 `GET /api/pricing` 本身"
               "匿名可读，实测 200）。页内 `fetch('/api/pricing')` 匿名**可用**，"
               "且页面**从 API 渲染**（不硬编码价格 ⇒ 不是第二个事实源）。"
               "**这正是它与 `/membership` 的关键差别**：那个页面的按钮"
               "`/api/membership/...` 必然 401（k61 删它的理由就是「全动作失效」）。",
    },
    "src/api/share.py::/share": {
        "auth": "anon", "kind": "html", "verdict": "③",
        "why": "对话分享落地页（`?id=`）。**必须匿名**：微信扫码/浏览器直接打开，"
               "都带不了 Authorization 头。已按 k76 剥离个人信息、按 k76/k77 给"
               "30 天有效期 + 过期 fail-closed（410 Gone，不返内容）。",
    },
    "src/api/share.py::/share/{reading_id}": {
        "auth": "anon", "kind": "html", "verdict": "③",
        "why": "报告分享页。同 `/share`：匿名是**功能前提**（分享接收者没有令牌，"
               "而 k76 起 `/report/{id}` 要鉴权+归属）。k76 中性标题 + "
               "`_redact_report_for_share` 剥离个人信息；k77-F 同款 30 天有效期。",
    },
    "src/api/visual_report.py::/report/{reading_id}": {
        "auth": "auth", "kind": "html", "verdict": "②已具备",
        "why": "本人路径：`Depends(require_user)` + `assert_report_owner`。"
               "返 HTML 只为「浏览器直接访问时人可读」（403/401 用极简页，"
               "接口状态码语义不变）⇒ 不是无鉴权面，登记备查。",
    },
    "src/main.py::/api/chat/uploads/{filename}": {
        "auth": "anon", "kind": "file", "verdict": "③",
        "why": "对话图片静态读取。**必须匿名**：小程序 `<image src>` 与 handler 的"
               "`urlretrieve`（CV 面相/手相链路）都无法携带 Authorization 头。"
               "安全靠**能力式不可猜文件名**（服务端 `uuid4().hex + ext`，128 bit）"
               "+ `Path.name` 校验拒穿越（实测拒绝 `../` 形态）。",
    },
    "src/api/user.py::/api/user/avatar/{user_id}": {
        "auth": "anon", "kind": "file", "verdict": "③",
        "why": "头像。代码已声明语义「与微信头像公开语义一致」；`user_id` 经 "
               "`re.sub(r\"[^A-Za-z0-9_.-]\", \"_\", …)` 白名单化后才拼路径，"
               "并带 `X-Content-Type-Options: nosniff`。",
    },
}

#: StaticFiles 挂载面（AST 面之外的第二种"无鉴权静态挂载"）。
STATIC_MOUNTS = {
    "src/main.py::/share-cards": {
        "auth": "anon", "verdict": "②**已按拍板收窄**（k85 必修1）",
        "why": "`app.mount('/share-cards', ShareCardOnlyStaticFiles(CHARTS_DIR))`"
               "（目录存在才挂；**挂载 path / name / 匿名性逐字节未变** ⇒ 分享卡对外"
               "URL 零回归）。改前是裸 `StaticFiles`：目录里**混装**两类产物 —— "
               "① `share_{reading_id}.png`（发布者主动分享的卡片，本该公开）；"
               "② `bazi_{YYYYmmdd_HHMMSS}.png`（**私有命盘图**，**秒级时间戳 ⇒ 可枚举**、"
               "无 TTL）。k84 上报、控制方裁定「收窄挂载面」，k85 落地为三道闸："
               "① 挂载换成 `ShareCardOnlyStaticFiles`（`lookup_path` 只放行 "
               "`is_public_share_card`，其余一律 404）；"
               "② 私有图改落 `private_charts_dir()`（`CHARTS_DIR` 的**兄弟**目录，"
               "刻意不做子目录 —— 子目录会被同一条 nginx alias 一并命中）；"
               "③ 私有图只经 `GET /api/charts/{filename}`（`require_user` + 归属令牌）。"
               "另注（k85 实测）：`src/main.py` 原注释声称「与 /share-cards 同策略："
               "uuid 文件名不可猜测」，该假设对 `bazi_<ts>.png` **不成立**；"
               "且 `handler.py` 里硬编码的 `http://124.221.233.214/charts/...`"
               "**本仓从未挂载该路径** ⇒ k85 实测生产 404（死链），已一并清掉、改走"
               "本路由。",
    },
}


#: **面外的同类**：返回**二进制**（不是 HTML/FileResponse）的无鉴权 GET ——
#: AST 面抓不到（它用的是 `Response(content=…, media_type=…)`），但按审计口径
#: 「返回 HTML/文件的无鉴权路由」的**同类**，必须逐条判定。显式登记，运行面核。
BINARY_SURFACE = {
    "/api/share/qr": {
        "auth": "anon", "verdict": "③",
        "why": "分享卡二维码 PNG。签名 `(url: str = Query(...))`，**无鉴权依赖**。"
               "判定 ③：这是**功能前提** —— 二维码要画进分享卡、由**扫码的接收方**"
               "与分享图一起流转；且小程序侧走 `wx.downloadFile`（"
               "`miniprogram/utils/api.js::downloadShareQr`，**不带 Authorization 头**），"
               "收紧会静默降级分享卡（失败路径只是跳过二维码区域，不报错）。"
               "该端点唯一真实滥用面已被现有控制挡住：`url` 必须带 "
               "`https://yilichat.com/share?id=` 前缀（`_SHARE_QR_PREFIX`）且拒绝 "
               "控制字符/空格 ⇒ **不能**被当作开放二维码生成器（拿它编码任意钓鱼链接）。"
               "备选（登记，需拍板）：给 `downloadShareQr` 加 `header: {Authorization}` "
               "（`wx.downloadFile` 支持 header）后本端点即可加鉴权 —— 属前端行为改动，"
               "不在本批自决。",
    },
}


def _get_html_routes_in_source():
    """AST 面：`src/**/*.py` 里「GET 路由 + 函数体含 HTMLResponse/FileResponse」。"""
    hits = set()
    for p in sorted((REPO / "src").rglob("*.py")):
        text = p.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:                                  # pragma: no cover
            continue
        rel = str(p.relative_to(REPO)).replace(os.sep, "/")
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                    continue
                if dec.func.attr != "get" or not dec.args:
                    continue
                if not isinstance(dec.args[0], ast.Constant):
                    continue
                body = ast.get_source_segment(text, node) or ""
                if "HTMLResponse" in body or "FileResponse" in body:
                    hits.add(f"{rel}::{dec.args[0].value}")
    return hits


def _static_mounts_in_source():
    """AST 面：`app.mount(...)` 的**字面量**挂载点。"""
    hits = set()
    for p in sorted((REPO / "src").rglob("*.py")):
        text = p.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:                                  # pragma: no cover
            continue
        rel = str(p.relative_to(REPO)).replace(os.sep, "/")
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "mount" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and "StaticFiles" in (ast.get_source_segment(text, node) or "")):
                hits.add(f"{rel}::{node.args[0].value}")
    return hits


def _dep_names(route):
    """路由依赖链上的全部函数名（递归展开子依赖）。"""
    out = set()

    def walk(dep):
        for sub in getattr(dep, "dependencies", None) or []:
            call = getattr(sub, "call", None)
            if call is not None:
                out.add(getattr(call, "__name__", "") or "")
            walk(sub)

    walk(getattr(route, "dependant", None))
    return out


def _auth_of(app, path):
    """运行面：该 path 是否挂了鉴权依赖（require_user / require_chat_user）。"""
    for r in app.routes:
        if getattr(r, "path", None) == path:
            names = _dep_names(r)
            return "auth" if ({"require_user", "require_chat_user"} & names) else "anon"
    raise KeyError(f"路由不存在: {path}")


class TestT1HtmlSurfaceInventory:
    """T1：HTML/文件响应面 = 已登记清单（逐条判定），新增即红。"""

    def test_source_scan_matches_the_registered_inventory(self):
        found = _get_html_routes_in_source()
        assert found == set(HTML_SURFACE), (
            "返回 HTML/文件的路由清单与已登记表不一致（多出来的 = 未表态的新面）：\n"
            f"  未登记: {sorted(found - set(HTML_SURFACE))}\n"
            f"  已消失: {sorted(set(HTML_SURFACE) - found)}")

    def test_source_scan_is_not_blind(self):
        """判据自检：枚举器必须真的能看见东西（防"扫描器失效 ⇒ 空集 ⇒ 假绿"）。"""
        found = _get_html_routes_in_source()
        assert len(found) >= 7, f"AST 面只扫到 {len(found)} 条（改前实测 7 条）—— 枚举器可能失效"
        assert len(_static_mounts_in_source()) >= 1, "StaticFiles 挂载面扫描器失效"

    def test_static_mounts_match(self):
        assert _static_mounts_in_source() == set(STATIC_MOUNTS), (
            f"静态挂载面与已登记表不一致：{sorted(_static_mounts_in_source())}")

    def test_auth_disposition_matches_at_runtime(self):
        """**判定的鉴权状态用运行面实测**（不是手写）：逐条比对 app 上的真实依赖。"""
        import src.main as m
        bad = []
        for key, meta in {**HTML_SURFACE, **STATIC_MOUNTS}.items():
            path = key.split("::", 1)[1]
            if key in STATIC_MOUNTS:                # mount 不进 app.routes 的 path 索引
                continue
            got = _auth_of(m.app, path)
            if got != meta["auth"]:
                bad.append(f"{path}: 登记 {meta['auth']} / 实测 {got}")
        assert bad == [], "鉴权状态与登记不符（要么登记过期，要么鉴权被拿掉）：\n" + "\n".join(bad)

    def test_binary_surface_matches_at_runtime(self):
        """面外同类（二进制响应的无鉴权 GET）：登记 + 运行面逐条核实存在与鉴权。"""
        import src.main as m
        for path, meta in BINARY_SURFACE.items():
            assert any(getattr(r, "path", None) == path for r in m.app.routes), (
                f"{path} 已不在路由表（登记过期）")
            assert _auth_of(m.app, path) == meta["auth"], (
                f"{path} 的鉴权状态变了（登记 {meta['auth']}）——判定的前提没了，"
                "须重新评估该条判定")

    def test_qr_endpoint_still_pins_the_url_prefix(self):
        """`/api/share/qr` 判为 ③ 的前提 = "不能被当开放二维码生成器"，前提必须在。"""
        import src.api.share as share_api
        src = (REPO / "src" / "api" / "share.py").read_text(encoding="utf-8")
        assert share_api._SHARE_QR_PREFIX.startswith("https://"), \
            "_SHARE_QR_PREFIX 不再是本站 https 前缀 —— 该端点的滥用面控制被放开"
        assert "仅支持本站分享链接" in src and "非法链接" in src, \
            "二维码 URL 前缀/控制字符两道闸之一不见了"


class TestT1DeadPagesRemoved:
    """T1①：死页/孤儿页（判据同 k61-P4 删 /membership：无调用方 + 页内动作必然 401）。"""

    def test_scenarios_page_route_and_file_are_gone(self):
        import src.main as m
        assert _auth_of_absent(m.app, "/scenarios"), (
            "GET /scenarios 又回来了。它的页内唯一动作 `fetch('/api/chat')` 不带 "
            "Authorization ⇒ require_chat_user 必然 401（与 /membership 同型）")
        assert not (REPO / "src" / "static" / "scenarios.html").exists(), \
            "孤儿页 src/static/scenarios.html 又回来了"

    def test_compatibility_page_route_and_builder_are_gone(self):
        import src.main as m
        assert _auth_of_absent(m.app, "/compatibility"), (
            "GET /compatibility 又回来了。它的页内 `fetch('/api/compatibility')` 是 "
            "`Depends(require_user)` ⇒ 匿名必然 401（与 /membership 同型）")
        src = (REPO / "src" / "api" / "compatibility.py").read_text(encoding="utf-8")
        assert "_build_compatibility_html" not in src, "孤儿页构造器又回来了"
        assert "HTMLResponse" not in src, "compatibility.py 又引入了 HTMLResponse"

    def test_dead_web_client_file_is_gone(self):
        assert not (REPO / "src" / "chat.html").exists(), (
            "src/chat.html 又回来了：它**从未被任何路由服务**（`/chat` 路由全仓"
            "零命中，`git log -S'@app.get(\"/chat\")'` 也是零）⇒ 死文件")

    def test_api_scenarios_json_is_untouched(self):
        """反向钉住：删 HTML 死页**不得**误伤小程序真正用的 JSON 接口。"""
        import src.main as m
        assert any(getattr(r, "path", None) == "/api/scenarios" for r in m.app.routes), \
            "/api/scenarios（JSON，小程序在用）被误删了"


def _auth_of_absent(app, path):
    return not any(getattr(r, "path", None) == path for r in app.routes)


class TestT1DocsFailClosed:
    """T1②：FastAPI 自带的 /docs、/redoc、/openapi.json 收口（fail-closed 默认关闭）。"""

    def test_docs_are_disabled_by_default(self):
        import src.main as m
        assert m.app.docs_url is None, "app.docs_url 不是 None（/docs 默认又开了）"
        assert m.app.redoc_url is None, "app.redoc_url 不是 None（/redoc 默认又开了）"
        assert m.app.openapi_url is None, (
            "app.openapi_url 不是 None —— /openapi.json 会把**全量接口清单**"
            "无鉴权交出去（本仓口径：全接口鉴权、不允许接口暴露）")

    def test_docs_routes_are_absent_from_the_route_table(self):
        import src.main as m
        for p in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
            assert _auth_of_absent(m.app, p), f"{p} 仍在路由表里（应默认关闭）"

    def test_docs_open_only_with_explicit_opt_in(self):
        """显式 opt-in 时三个路由回来（开关是**可逆**的，不是把能力删了）。"""
        env = dict(os.environ, FORTUNE_ENABLE_DOCS="1")
        code = (
            "import src.main as m;"
            "print(m.app.docs_url, m.app.redoc_url, m.app.openapi_url)"
        )
        r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), env=env,
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stderr[-2000:]
        assert r.stdout.strip() == "/docs /redoc /openapi.json", (
            f"FORTUNE_ENABLE_DOCS=1 时文档没开起来：{r.stdout.strip()!r}")

    def test_docs_switch_is_documented(self):
        """开关必须写在文档里（否则线上没人知道怎么开/为什么关了）。"""
        api_md = (REPO / "docs" / "API.md").read_text(encoding="utf-8")
        assert "FORTUNE_ENABLE_DOCS" in api_md, "docs/API.md 未写明文档开关"


# ══════════════════════════════════════════════════════════════════════
# T2 必修7：/api/share/{report_id} 的形态白名单
# ══════════════════════════════════════════════════════════════════════

#: 复用 k80-M5 的 harness（**不复制一份**：同一个 `_client` 的隔离口径
#: ——报告目录 monkeypatch 到 tmp、`_dao` 注入、绝不碰生产库）。
def _k80():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "k80mod_for_k84", REPO / "tests" / "test_k80_xss_privacy_final.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _client(tmp_path, monkeypatch, dao=None):
    return _k80()._client(tmp_path, monkeypatch, dao=dao)


def _write(tmp_path, rid, payload, owner_uid="k80-owner"):
    return _k80()._write(tmp_path, rid, payload, owner_uid=owner_uid)


_REPORT = {"profile": {"name": "张三", "bazi": "甲子 乙丑 丙寅 丁卯"}, "insights": ["洞察一"]}


class TestT2ShareIdShapeWhitelist:
    """T2：形态白名单**前置**（不读盘、不查库），且**正常路径一条不受影响**。"""

    def test_normal_paths_still_work(self, tmp_path, monkeypatch):
        """三种合法形态逐个走通 —— 这就是"加了之后正常路径不受影响"的实测。"""
        client, h = _client(tmp_path, monkeypatch)

        # ① 非全数字的 8 位 hex（常规 reading_id）
        _write(tmp_path, "abcd1234", _REPORT)
        r = client.get("/api/share/abcd1234", headers=h)
        assert r.status_code == 200, f"常规 reading_id → {r.status_code}"
        assert r.json()["reading_id"] == "abcd1234"

        # ② 全数字的 8 位 hex（k80-M5 修过的那 2.33% 情形）—— 必须仍走**报告**分支
        _write(tmp_path, "12345678", _REPORT)
        r = client.get("/api/share/12345678", headers=h)
        assert r.status_code == 200, f"全数字 reading_id → {r.status_code}"
        assert r.json()["reading_id"] == "12345678", "全数字 hex 又被误当咨询 ID 了"

        # ③ 纯数字咨询 ID —— 必须仍走**咨询**分支（DAO 收到原样的 int）
        class _Dao:
            def __init__(self):
                self.calls = []

            def get_consultation(self, cid):
                self.calls.append(cid)
                return None

        dao = _Dao()
        client2, h2 = _client(tmp_path, monkeypatch, dao=dao)
        assert client2.get("/api/share/987654", headers=h2).status_code == 404
        assert dao.calls == [987654], "数字 ID 没走到咨询分支"

    def test_off_shape_is_rejected_without_any_disk_or_db_probe(self, tmp_path, monkeypatch):
        """怪串 → 404，**且不触发任何读盘/查库**（白名单前置的意义所在）。"""
        import src.api.share as share_api
        client, h = _client(tmp_path, monkeypatch)

        probed = []
        real_load = share_api._load_report

        def _spy(rid):
            probed.append(rid)
            return real_load(rid)

        monkeypatch.setattr(share_api, "_load_report", _spy)

        class _Dao:
            def __init__(self):
                self.calls = []

            def get_consultation(self, cid):        # pragma: no cover - 不该被调用
                self.calls.append(cid)
                return None

        dao = _Dao()
        client2, h2 = _client(tmp_path, monkeypatch, dao=dao)

        weird = ["../etc/passwd", "..%2f..%2fetc", "a" * 300, "notahexid",
                 "ABCD1234", "abcd123", "abcd12345", "１２３", "²", "0x10", "1e5",
                 " abcd1234", "abcd1234 ", "abcd1234/../x"]
        for bad in weird:
            st = client2.get(f"/api/share/{bad}", headers=h2).status_code
            assert st == 404, f"/api/share/{bad!r} → {st}（应 404，且**不是** 500）"
        assert probed == [], f"怪串触发了读盘：{probed}"
        assert dao.calls == [], f"怪串触发了查库：{dao.calls}"

    def test_ascii_digit_gate_closes_the_isdigit_500(self, tmp_path, monkeypatch):
        """`str.isdigit()` 对 `²` 为真而 `int('²')` 抛 ValueError（实测）⇒ 改前 500。

        白名单改用 ASCII 的 `^[0-9]+$` 后归入 404。这条钉住"不是 500"。
        """
        assert "²".isdigit() and "１２３".isdigit()          # 前提：Python 的行为
        client, h = _client(tmp_path, monkeypatch)
        for bad in ("²", "１２３"):
            assert client.get(f"/api/share/{bad}", headers=h).status_code == 404, bad

    def test_whitelist_uses_the_same_regex_objects_as_the_rest_of_the_module(self):
        """口径一致：白名单与下游分派**共用** `_READING_ID_RE` / `_CONSULT_ID_RE`。

        用 **AST** 判（不是文本 grep）：docstring 里**引述**历史写法
        `report_id.isdigit()`（本条 why 里就引了）不算回归。
        """
        import src.api.share as share_api
        assert share_api._READING_ID_RE.pattern == r"^[0-9a-f]{8}$", \
            "_READING_ID_RE 形态变了（必须与 /share/{id}、/report/{id} 同口径）"
        assert share_api._CONSULT_ID_RE.pattern == r"^[0-9]+$", \
            "_CONSULT_ID_RE 应为 ASCII 数字（不能用 str.isdigit）"

        src = (REPO / "src" / "api" / "share.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        # ① 代码里不得再有 `X.isdigit()` 形态的判据（`²` 会穿过它再在下游抛 ValueError）
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "isdigit"]
        assert calls == [], (
            f"share.py 的**代码**里又出现 `.isdigit()` 判据（行 "
            f"{[c.lineno for c in calls]}）—— 与白名单判据漂移")
        # ② 分派确实走了与白名单同一个正则对象
        segs = [ast.get_source_segment(src, n) or "" for n in ast.walk(tree)
                if isinstance(n, ast.Call)]
        assert any("_CONSULT_ID_RE.match(report_id)" in s for s in segs), (
            "咨询分支没走 `_CONSULT_ID_RE.match(report_id)`（判据没收敛到一处）")

    def test_no_existing_share_assertion_was_loosened(self):
        """交叉印证：k79/k80 既有的分享断言一条都没被放松（只加不减）。"""
        k80 = (REPO / "tests" / "test_k80_xss_privacy_final.py").read_text(encoding="utf-8")
        assert "test_unknown_shape_is_404" in k80
        assert "TestShareDispatchByExistenceNotShape" in k80
        k79 = (REPO / "tests" / "test_k79_privacy_security_final2.py").read_text(encoding="utf-8")
        assert "test_long_id_on_share_routes_is_404_not_500" in k79
        assert "ROUTES = (\"/share/{rid}\", \"/api/share/{rid}\"" in k79
