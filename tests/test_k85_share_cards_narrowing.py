# -*- coding: utf-8 -*-
"""k85 必修1 门禁：`/share-cards` 收窄挂载面 + 私有命盘图鉴权路由。

## 本文件钉住什么

控制方裁定（k84 上报后拍板）：「**收窄挂载面**」—— `/share-cards` 只公开
`share_*.png`；`bazi_*`（及 `ziwei_*` / `fengshui_*`）私有图走**鉴权路由**
（复用既有 `require_user` + 归属校验口径）。

四条不变量（每条一个用例，全部可复跑）：
  ① **私有图匿名取不到**（`/api/charts/*` 无令牌 → 401；走旧挂载面 → 404）；
  ② **分享卡匿名仍可取**（`/share-cards/share_*.png` → 200）—— **回归红线**；
  ③ **归属校验**：本人的图 → 200；别人的图 / 不存在 → 404；`.html` 恒 404；
  ④ **落盘口径**：私有图落 `private_charts_dir()`（= `CHARTS_DIR` 的兄弟目录，
     不是子目录、更不在公开面里），文件名带 HMAC 归属令牌。

红线：不联网、不碰生产库、不新增 skip/xfail、未放宽任何既有断言
（`tests/test_k84_sweep_final.py` 的 StaticFiles 清单断言**原样通过** ——
挂载 path / name / 匿名性都没变，只换了挂在同一个点上的类）。
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

from src.images import chart_files as cf  # noqa: E402

OWNER = "k85-owner-user"
OTHER = "k85-other-user"
PNG = b"\x89PNG\r\n\x1a\n" + b"k85-fixture"


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """把公开面 / 私有面都指到 tmp（不碰任何真实目录）。"""
    pub, priv = tmp_path / "charts", tmp_path / "charts_private"
    pub.mkdir()
    priv.mkdir()
    monkeypatch.setenv("CHARTS_DIR", str(pub))
    monkeypatch.setenv("PRIVATE_CHARTS_DIR", str(priv))
    return pub, priv


def _write(path: Path, data: bytes = PNG):
    path.write_bytes(data)
    return path


# ══════════════════════════════════════════════════════════════════════
# ① 挂载面收窄：只放行 share_*.png（真挂载类 + 真 TestClient）
# ══════════════════════════════════════════════════════════════════════

def _mount_app(pub: Path) -> TestClient:
    """用**仓内真实**的 `ShareCardOnlyStaticFiles` 起一个最小 app。

    刻意不 import `src.main`：那个模块的挂载是"目录存在才挂"的（与改前同口径），
    在测试环境里可能没挂上；而"收窄判据"这件事本身应当能被**直接**钉住。
    真实路由 `GET /api/charts/{filename}` 另在下组用例里直呼真函数。
    """
    from src.main import ShareCardOnlyStaticFiles
    app = FastAPI()
    app.mount("/share-cards", ShareCardOnlyStaticFiles(directory=str(pub)),
              name="share-cards")
    return TestClient(app)


class TestMountIsNarrowed:
    def test_share_cards_still_served_anonymously(self, dirs):
        """**回归红线**：分享卡（发布者主动公开的东西）匿名必须仍然可取。"""
        pub, _ = dirs
        _write(pub / "share_abcd1234.png")
        _write(pub / "share_12345678.png")
        _write(pub / "share_20260923_120000.png")
        c = _mount_app(pub)
        for name in ("share_abcd1234.png", "share_12345678.png",
                     "share_20260923_120000.png"):
            r = c.get(f"/share-cards/{name}")
            assert r.status_code == 200, f"{name} → {r.status_code}（分享卡被收窄误伤）"
            assert r.content == PNG

    def test_private_charts_are_not_served_by_the_mount(self, dirs):
        """私有图（含**无令牌的历史遗留**形态）一律 404 —— 匿名面收窄。"""
        pub, _ = dirs
        tok = cf.owner_token(OWNER)
        for name in (f"bazi_20260923_120000_{tok}.png",
                     "bazi_20250101_010101.png",          # 遗留：改前就躺在公开目录里
                     "ziwei_20260923_120000.png",
                     "fengshui_20260923_120000.png",
                     f"bazi_20260923_120000_{tok}.html",
                     "anything.txt", "notes.md"):
            _write(pub / name)
            r = _mount_app(pub).get(f"/share-cards/{name}")
            assert r.status_code == 404, f"{name} 被匿名服务出去了 → {r.status_code}"

    def test_no_directory_listing(self, dirs):
        pub, _ = dirs
        _write(pub / "share_abcd1234.png")
        assert _mount_app(pub).get("/share-cards/").status_code == 404

    def test_path_traversal_is_rejected(self, dirs):
        pub, _ = dirs
        _write(pub / "share_abcd1234.png")
        secret = pub.parent / "outside.png"
        _write(secret)
        c = _mount_app(pub)
        for probe in ("/share-cards/../outside.png",
                      "/share-cards/..%2foutside.png",
                      "/share-cards/%2e%2e/outside.png"):
            assert c.get(probe).status_code == 404, f"穿越成功：{probe}"


# ══════════════════════════════════════════════════════════════════════
# ② 鉴权路由（直呼真函数：`require_user` 由 FastAPI 注入 uid）
# ══════════════════════════════════════════════════════════════════════

def _call_chart(filename: str, uid: str):
    from src.main import get_private_chart
    try:
        return asyncio.run(get_private_chart(filename, uid))
    except HTTPException as e:
        return e


class TestPrivateChartRoute:
    def test_owner_gets_their_own_chart(self, dirs):
        _, priv = dirs
        tok = cf.owner_token(OWNER)
        p = _write(priv / f"bazi_20260923_120000_{tok}.png")
        resp = _call_chart(p.name, OWNER)
        assert not isinstance(resp, HTTPException), resp
        assert Path(resp.path) == p
        assert resp.media_type == "image/png"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_anonymous_is_rejected_by_auth_layer(self, dirs):
        """匿名 ⇒ 401（`require_user`）。**运行面**核实：路由确实挂了鉴权依赖。"""
        import src.main as m
        route = next(r for r in m.app.routes
                     if getattr(r, "path", None) == "/api/charts/{filename}")
        names = set()

        def walk(dep):
            for sub in getattr(dep, "dependencies", None) or []:
                call = getattr(sub, "call", None)
                if call is not None:
                    names.add(getattr(call, "__name__", "") or "")
                walk(sub)

        walk(getattr(route, "dependant", None))
        assert "require_user" in names, f"私有图路由没挂 require_user：{sorted(names)}"

    def test_other_users_cannot_fetch(self, dirs):
        _, priv = dirs
        p = _write(priv / f"bazi_20260923_120000_{cf.owner_token(OTHER)}.png")
        got = _call_chart(p.name, OWNER)
        assert isinstance(got, HTTPException) and got.status_code == 404

    def test_html_is_never_served(self, dirs):
        """`.html` 是渲染中间产物（可含未转义文本）⇒ 本人也不发。"""
        _, priv = dirs
        tok = cf.owner_token(OWNER)
        p = _write(priv / f"bazi_20260923_120000_{tok}.html", b"<html>x</html>")
        got = _call_chart(p.name, OWNER)
        assert isinstance(got, HTTPException) and got.status_code == 404

    def test_off_shape_and_missing_are_404(self, dirs):
        tok = cf.owner_token(OWNER)
        for bad, uid in (("bazi_20250101_010101.png", OWNER),      # 无令牌（遗留）
                         ("../../etc/passwd", OWNER),
                         ("bazi_20260923_120000_zzzzzzzzzzzzzzzz.png", OWNER),
                         ("share_abcd1234.png", OWNER),            # 公开面形态
                         (f"bazi_20260923_120000_{tok}.png", "")):  # 无 uid
            got = _call_chart(bad, uid)
            assert isinstance(got, HTTPException) and got.status_code == 404, \
                f"{bad!r} uid={uid!r} → {got!r}"


# ══════════════════════════════════════════════════════════════════════
# ③ 落盘口径 + URL 口径（单一事实源）
# ══════════════════════════════════════════════════════════════════════

class TestSingleSourceOfTruth:
    def test_private_dir_is_a_sibling_not_a_child(self, dirs):
        pub, priv = dirs
        assert cf.private_charts_dir() == priv
        assert priv.parent == pub.parent, "私有面必须是公开面的兄弟目录"
        assert pub not in priv.parents or priv.parent == pub.parent

    def test_private_path_carries_owner_token(self, dirs):
        p = cf.private_chart_path("bazi", OWNER)
        assert p.parent == cf.private_charts_dir(), "私有图不许落进公开面"
        assert cf.verify_owner(p.name, OWNER)
        assert not cf.verify_owner(p.name, OTHER)
        assert not cf.is_public_share_card(p.name)

    def test_reply_url_only_when_png_and_owned(self, dirs):
        p = cf.private_chart_path("bazi", OWNER)
        url = cf.reply_chart_url(str(p), OWNER)
        assert url.endswith("/api/charts/" + p.name)
        assert "/share-cards/" not in url, "私有图 URL 不许指到公开挂载面"
        assert cf.reply_chart_url(str(p), "") == "", "无归属不许发 URL（随机令牌取不到）"
        assert cf.reply_chart_url(str(p).replace(".png", ".html"), OWNER) == "", \
            ".html 不许发 URL（私有路由不发 .html ⇒ 发出去是死链）"

    def test_public_url_stays_on_the_share_mount(self):
        u = cf.public_share_card_url("share_abcd1234.png")
        assert u.endswith("/share-cards/share_abcd1234.png")

    def test_purge_only_touches_the_owner(self, dirs):
        _, priv = dirs
        mine = (_write(priv / f"bazi_20260923_120000_{cf.owner_token(OWNER)}.png"),
                _write(priv / f"ziwei_20260923_120000_{cf.owner_token(OWNER)}.png"))
        theirs = _write(priv / f"bazi_20260923_120000_{cf.owner_token(OTHER)}.png")
        orphan = _write(priv / "bazi_20260923_120000_0000000000000000.png")
        n = cf.purge_private_charts(OWNER)
        assert n == len(mine) == 2, f"应删 2 个，实际 {n}"
        assert not any(p.exists() for p in mine)
        assert theirs.exists() and orphan.exists(), "删到了别人的图（归属判据失效）"


# ══════════════════════════════════════════════════════════════════════
# ④ 生成器：插值转义（`.html` 里的 XSS 通路，源头修复）
# ══════════════════════════════════════════════════════════════════════

class TestChartHtmlEscaping:
    """强制 playwright 不可用 ⇒ `generate` 保留并返回 `.html`，可直读校验。

    这**正好**也是那个降级环境的真实形态（生产目录里只有 .png、无 .html，
    说明线上 playwright 可用 ⇒ 本条属**潜伏**面，非线上在发生）。
    """

    def test_injected_title_is_escaped(self, tmp_path, monkeypatch):
        import src.images.bazi_chart_html as M
        from src.engines.bazi import BaziEngine
        monkeypatch.setattr(M, "_playwright_available", lambda: False)
        res = BaziEngine().calculate(1999, 3, 28, 10, 55, "长春", "男")
        inj = "</title><script>alert(1)</script>"
        out = M.BaziChartHTML().generate(res, output_path=str(tmp_path / "c.png"),
                                        title=inj)
        html = Path(out).read_text(encoding="utf-8")
        assert "<script>alert(1)</script>" not in html, "注入的 <script> 原样落地"
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html, "没有转义"

    def test_normal_data_is_byte_identical(self, tmp_path, monkeypatch):
        """正常数据下 `autoescape` 是**渲染无变化**的（不是行为改动）。

        实测（真 playwright）两条口径的 PNG sha256 完全相同；这里用不需要
        playwright 的等价判据：产物里不得出现任何转义实体（正常数据无特殊字符）。
        """
        import src.images.bazi_chart_html as M
        from src.engines.bazi import BaziEngine
        monkeypatch.setattr(M, "_playwright_available", lambda: False)
        res = BaziEngine().calculate(1999, 3, 28, 10, 55, "长春", "男")
        out = M.BaziChartHTML().generate(res, output_path=str(tmp_path / "c.png"),
                                        title="我的命盘")
        html = Path(out).read_text(encoding="utf-8")
        assert "&lt;" not in html and "&gt;" not in html and "&#" not in html, \
            "正常数据被转义出了实体 ⇒ 与改前渲染不一致（会改变截图）"
        assert "我的命盘" in html

    def test_generated_chart_lands_in_the_private_dir(self, dirs, monkeypatch):
        """缺省落盘（不传 output_path）⇒ 私有面 + 归属令牌，**绝不进公开面**。"""
        import src.images.bazi_chart_html as M
        from src.engines.bazi import BaziEngine
        monkeypatch.setattr(M, "_playwright_available", lambda: False)
        res = BaziEngine().calculate(1999, 3, 28, 10, 55, "长春", "男")
        pub, priv = dirs
        out = M.BaziChartHTML().generate(res, title="t", user_id=OWNER)
        p = Path(out)
        assert p.parent == priv, f"缺省落盘不在私有面：{p}"
        assert p.suffix == ".png" or p.suffix == ".html"
        assert cf.verify_owner(p.name, OWNER), "落盘文件名里没有归属令牌"
        assert not any(pub.iterdir()), f"公开面被写进了东西：{list(pub.iterdir())}"


# ══════════════════════════════════════════════════════════════════════
# k86 必修4：私有图 URL 收窄后，回复里那行**裸 URL** 成了死承诺
# ══════════════════════════════════════════════════════════════════════
# 背景（k85 指出、k86 判定并落地）：
#   k85 把私有命盘图收窄到 `GET /api/charts/{filename}`（`require_user` + 归属令牌）
#   之后，回复里那行 `📊 命盘图片：<url>` **对谁都打不开**：
#     · 小程序端把回复按纯文本渲染（`miniprogram/` 对 `/api/charts`、`命盘图片`
#       **零引用**）⇒ 既不可点，也没有任何代码去取它；
#     · 复制到站外 → 没有 Authorization 头 → **401**（用户本人也一样）。
#   k86 判定**暂不做**「点开即看」（前端 `wx.downloadFile` 带 header），理由：
#     ① 本环境做不了真机/模拟器渲染取证（无 DevTools 自动化会话），而"改前端必须有
#        真实渲染验证"是硬规则；② `wx.downloadFile` 需要**单独的**「downloadFile
#        合法域名」后台配置，仓内唯一调用点（`pages/reports/reports.js:158`）只取
#        **公开**分享卡且**不带 header**，私有下载链路在本 App 从未验证 —— 盲发会把
#        "看得见的死链"换成"点了静默失败"；③ 这是功能开发（气泡 + 401 处理 + 查看器），
#        属产品拍板范围。
#   落地 = **去掉死 URL，改为明确的提示文案**，且图仍生成、私有面与归属令牌不变。
# 本类钉住：死 URL 不得回到用户可见文本里，且新提示不得漏进 LLM 上下文。


class TestPrivateChartReplyHasNoDeadUrl:
    def test_user_facing_hint_contains_no_url(self):
        from src.bot import handler as H
        for name in ("CHART_HINT_PRIVATE", "FENGSHUI_HINT_PRIVATE"):
            text = getattr(H, name)
            assert "http" not in text and "//" not in text, (
                f"{name} 里又出现了 URL —— 私有图要鉴权头，裸 URL 对用户是死链")
            assert "私人图" in text, f"{name} 未说明是私人图"
            assert "已生成" in text, f"{name} 未说明图已生成"

    def test_reply_assembly_does_not_interpolate_a_url(self):
        """注入点必须是**常量提示**，不得再 f-string 插 URL。"""
        src = (Path(__file__).resolve().parents[1]
               / "src" / "bot" / "handler.py").read_text(encoding="utf-8")
        assert 'reply += f"\\n\\n📊 命盘图片：{chart_url}"' not in src, \
            "八字/紫微回复又开始插私有图裸 URL"
        assert 'reply += f"\\n\\n📊 风水九宫图：{chart_url}"' not in src, \
            "风水回复又开始插私有图裸 URL"
        assert "CHART_HINT_PRIVATE" in src and "FENGSHUI_HINT_PRIVATE" in src

    def test_polished_reply_keeps_the_hint_once_and_never_a_url(self):
        """**行为面**：走真实 `_polish_with_engine_draft`，输出既留得住提示、也不含 URL。

        两条路径都测：LLM 正常输出（提示靠"保底重挂"回来）、LLM 仿写整段图行（净化）。
        """
        from types import SimpleNamespace
        from unittest.mock import patch
        from src.bot import handler as H

        h = object.__new__(H.MessageHandler)
        h.llm = SimpleNamespace(api_key="k", model="m")
        h._citations = {"u1": []}
        h.session_dao = None

        body = "行，我给你重新排一次。你命里火土燥，水是你的解药。"
        draft = body + "\n\n" + H.CHART_HINT_PRIVATE + "\n\n" + H._FEEDBACK_PROMPT
        imitation = (body + "\n" + H.CHART_HINT_PRIVATE
                     + "\n[/card]\n\n———\n这个分析对你有帮助吗？可回复「准」或「不准」告诉我")
        for label, llm_out in (("正常输出", body), ("仿写图行", imitation)):
            with patch("src.llm.client.deepseek_anthropic_completion",
                       return_value=llm_out):
                out = h._polish_with_engine_draft("帮我排盘", "u1", draft)
            assert "http" not in out, f"[{label}] 回复里出现了 URL：{out!r}"
            assert out.count("命盘图已生成") == 1, f"[{label}] 提示不是恰一次：{out!r}"
            assert "[/card]" not in out, f"[{label}] 假闭合没剥净：{out!r}"

    def test_hint_is_stripped_from_the_llm_context(self):
        """图行是**渲染装饰**，必须从喂给 LLM 的上下文里剥掉（否则会被仿写）。"""
        from src.bot.card_mark import strip_card_decor_for_llm
        from src.bot import handler as H
        draft = "正文。\n\n" + H.CHART_HINT_PRIVATE
        assert "命盘图已生成" not in strip_card_decor_for_llm(draft), \
            "新图行漏进 LLM 上下文 —— 会像 k7b 的假图行一样被仿写"
        # k7b 的既有假阳性样例不得被误剥（口径未变）
        assert "命盘图片：明天整理好再发你。" in \
            strip_card_decor_for_llm("命盘图片：明天整理好再发你。"), \
            "k7b 的「命盘图片：…（无 URL）」假阳性样例被误剥 —— 剥面被放宽了"

    def test_legacy_url_form_still_recovered_and_stripped(self):
        """历史稿（带 URL 的旧图行）仍能被保底找回与剥离 —— 兼容面只增不减。"""
        from src.bot.card_mark import strip_card_decor_for_llm
        legacy = "📊 命盘图片：https://yilichat.com/api/charts/bazi_x.png"
        assert legacy not in strip_card_decor_for_llm("正文。\n\n" + legacy), \
            "旧形态图行不再被剥离 —— k7b 的剥面被收窄了"

    def test_old_dead_hardcoded_host_never_returns(self):
        """k85 清掉的**死链形态**（硬编码 IP + 未挂载的 `/charts/`）不得复活。

        判据只扫**代码行**（剥掉整行注释）：`124.221.233.214` 在别处有正当用法
        （`src/eval/engine.py` 的评测靶机默认 URL），且 k85/k86 的说明注释里
        **引述**旧写法用于留痕 —— 那是记录历史，不是复活死链。真正要禁的是
        "把 `IP + /charts/` 拼成可下发 URL"这件事本身。
        """
        root = Path(__file__).resolve().parents[1]
        code = []
        for p in (root / "src").rglob("*.py"):
            in_doc = False
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if '"""' in line or "'''" in line:
                    # 三引号开关（本仓 docstring 不混用引号风格，够用且可读）
                    in_doc = not in_doc
                    continue
                if in_doc or line.strip().startswith("#"):
                    continue
                if "124.221.233.214" in line and "/charts/" in line:
                    code.append(f"{p.relative_to(root)}:{i}")
        assert code == [], f"死链形态（硬编码 IP + /charts/）又出现在代码里：{code}"

    def test_the_chart_png_is_still_generated_and_owner_gated(self):
        """去掉死 URL **不等于**放弃私有图：图仍生成、仍落私有面、仍带归属令牌。

        这条钉住"改动只动了用户可见文案，没削弱 k85 的收窄挂载面"。
        """
        from src.bot import handler as H
        src = (Path(__file__).resolve().parents[1]
               / "src" / "bot" / "handler.py").read_text(encoding="utf-8")
        assert "reply_chart_url" in src, \
            "`reply_chart_url` 被删了 —— 私有图的归属令牌链路不该被砍掉"
        assert src.count("if chart_url:") >= 3, \
            "`chart_url` 不再作为「要不要提示图已生成」的判据（可能被当成死变量删了）"
        assert hasattr(H, "CHART_HINT_PRIVATE")
