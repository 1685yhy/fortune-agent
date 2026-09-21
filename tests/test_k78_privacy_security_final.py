"""k78 隐私/安全收尾批回归门禁 —— 与 k77-integrate 最终复审报出的 4 条逐条对应。

覆盖（每条对应一处改动，防回退）：
  T1 **必修1**『注销时分享链接立即删除』的**例外**：`purge_report_files` 对"归属未知
     的老报告"刻意不动（这是**设计**，不是 bug）⇒ 文案必须写明该例外。本组用
     **行为**证明"归属未知者确实仍在"（所以文案若不写例外就是假话），并钉住
     四处文案都写明了例外。
  T2 **必修2** 后端用户可见文案：登录 403 / 注销响应 / 数据删除响应三处的
     **实际返回值**必须带『依法留存支付流水』例外、不得出现『所有个人数据』与
     『不可恢复』（与备份口径冲突）；文案取自 `src/security/account_copy.py`
     同一常量（单一事实源）。
     ⚠️ 扫描面（"后端所有 detail=/message= 字面量"的系统性拦网）在
     `miniprogram/tests/k71_privacy_consistency.test.js` §14 —— 那是**唯一**的规则表，
     本文件不复制一份（两份必然漂移），只做行为断言 + 常量接线断言。
  T3 **必修3** 畸形 JSON 打报告分享页/报告页 ⇒ **404（不是 500）**：
     `[1,2,3]` / `"just a string"` / `12345` / `{"profile":[1,2]}` /
     `{"generated_at":["x"]}` 五种，以及『归属正确但内容畸形』的分享卡片路径。
  T4 **必修4** `session_dao=None` 时 `POST /api/chat/sessions/delete` → **503**
     （与周边端点同口径；改前 500）；报告过期页 `<title>` 准确（改前是对话分享的
     『易理明灯 · 一段对话』）。

红线：不触网、不开生产库（库路径走 conftest 的沙箱 + tmp_path；报告目录 monkeypatch
到 tmp）；不新增 skip/xfail；未删改任何既有断言。
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

REPO = Path(__file__).resolve().parent.parent


# ── 共用小工具 ─────────────────────────────────────────────────────────

def _report_payload(reading_id: str, name: str = "用户") -> dict:
    """最小可用报告（字段形状与生产 data/reports/*.json 一致）。"""
    return {
        "reading_id": reading_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "generated_date": "",
        "version": "v5.0",
        "profile": {"name": name, "bazi": "甲子 乙丑 丙寅 丁卯", "day_master": "甲木",
                    "birth_date": "1990年5月20日", "birth_info": "1990年5月20日",
                    "gender": "男"},
        "bazi_analysis": {"wuxing": {"金": 1}, "shishen": [], "geju": "x",
                          "yongshen": "x", "shensha": [], "nayin": [],
                          "dayun": [], "liunian": {}},
        "charts": {"monthly_fortune": [], "annual_trend": [], "wuxing_radar": []},
        "insights": ["洞察内容"], "recommendations": ["建议内容"],
    }


def _write_report(dirpath: Path, reading_id: str, *, raw=None, owner_uid=None,
                  age_days=0) -> Path:
    """写一份报告文件。raw 给了就按 raw 原样写（畸形用例），否则写正常报告。"""
    if raw is not None:
        body = json.dumps(raw, ensure_ascii=False)
    else:
        data = _report_payload(reading_id)
        if owner_uid:
            from src.api.visual_report import _REPORT_OWNER_FIELD
            from src.security.encryption import DataEncryptor
            data[_REPORT_OWNER_FIELD] = DataEncryptor().encrypt(owner_uid)
        if age_days:
            data["generated_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - age_days * 86400))
        body = json.dumps(data, ensure_ascii=False)
    p = dirpath / f"{reading_id}.json"
    p.write_text(body, encoding="utf-8")
    if age_days:
        ts = time.time() - age_days * 86400
        os.utime(p, (ts, ts))
    return p


def _share_client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from src.api import share as share_api
    monkeypatch.setattr(share_api, "_DATA_DIR", tmp_path)
    app = FastAPI()
    app.include_router(share_api.router)
    return TestClient(app, raise_server_exceptions=False), share_api


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ══════════════════════════════════════════════════════════════════════
# T1 必修1：注销即删分享链接的**例外**（归属未知的老报告）
# ══════════════════════════════════════════════════════════════════════

class TestCancelKeepsUnownedReportsSoCopyMustSaySo:
    def test_owned_report_is_deleted_unowned_is_kept(self, tmp_path, monkeypatch):
        """本人报告随注销删；**归属未知的老报告不动** —— 这就是文案必须写例外的事实。"""
        uid = "k78-owner"
        reports = tmp_path / "reports"
        charts = tmp_path / "charts"
        reports.mkdir(parents=True, exist_ok=True)
        charts.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("CHARTS_DIR", str(charts))
        mine = _write_report(reports, "aaaa1111", owner_uid=uid)
        legacy = _write_report(reports, "bbbb2222")          # 无 owner_enc = 归属未知
        others = _write_report(reports, "cccc3333", owner_uid="someone-else")
        (charts / "share_aaaa1111.png").write_bytes(b"png")
        (charts / "share_bbbb2222.png").write_bytes(b"png")

        from src.storage import dao as dao_mod
        stats = dao_mod.purge_report_files(uid, reports_dir=reports, charts_dir=charts)

        assert stats["reports"] == 1, "注销没有删掉本人的报告文件"
        assert not mine.exists(), "本人的报告文件仍在（公开面未消失）"
        assert not (charts / "share_aaaa1111.png").exists(), "本人的分享图仍在"
        assert legacy.exists(), "归属未知的老报告被删了（越界：可能是别人的报告）"
        assert (charts / "share_bbbb2222.png").exists()
        assert others.exists(), "别人的报告被删了（越界）"

    def test_unowned_report_share_page_still_served(self, tmp_path, monkeypatch):
        """归属未知的老报告分享页**注销后仍可打开**（复审实测 200）——
        所以『注销时立即删除』这句话对它是假的，文案必须写明例外。"""
        client, _ = _share_client(tmp_path, monkeypatch)
        _write_report(tmp_path, "bbbb2222")
        r = client.get("/share/bbbb2222")
        assert r.status_code == 200, "归属未知的老报告分享页行为变了（文案与该行为必须一致）"

    def test_every_copy_surface_states_the_exception(self):
        """四处文案（privacy.md 三处 + privacy.wxml 两处 + settings.wxml 两处）都写明了例外。"""
        md = (REPO / "miniprogram" / "privacy.md").read_text(encoding="utf-8")
        pw = (REPO / "miniprogram" / "pages" / "privacy" / "privacy.wxml").read_text(
            encoding="utf-8")
        sw = (REPO / "miniprogram" / "pages" / "settings" / "settings.wxml").read_text(
            encoding="utf-8")
        # 例外词必须与『注销即删』的说法同时出现（同一份文件内 + 就近）
        cases = [
            ("privacy.md 第一节第 13 条", md, "已生成的分享链接会立即删除"),
            ("privacy.md 第五节第 3 条", md, "注销时立即删除"),
            ("privacy.md 第六节", md, "注销账号时立即删除"),
            ("privacy.wxml 注销范围段", pw, "注销时立即删除"),
            ("privacy.wxml 分享段", pw, "生成的分享链接会立即删除"),
            ("settings.wxml 安全与数据段", sw, "注销时你分享出去的链接会立即删除"),
            ("settings.wxml 注销弹层", sw, "你分享出去的链接会立即删除"),
        ]
        for label, text, needle in cases:
            assert needle in text, f"{label}：未找到该口径句（改动必须同步本断言）"
            i = text.index(needle)
            win = text[max(0, i - 240): i + len(needle) + 260]
            assert ("归属" in win) or ("老报告" in win), (
                f"{label}：写了无条件的『{needle}』，而就近处没有『归属未知/老报告』"
                "例外 —— 归属未知的老报告注销后仍在（见本类行为用例）")

    def test_every_copy_surface_has_no_unconditional_claim(self):
        """反方向：把这些文件里"注销…删除…分享/链接"的句子逐条取出，必须都能就近找到例外。"""
        import re
        pats = [
            re.compile(r"(注销|销号)[^。；\n]{0,24}(立即|即刻|马上)删除"),
            re.compile(r"(注销|销号)[^。；\n]{0,40}(分享|链接)[^。；\n]{0,20}删除"),
            re.compile(r"(分享|链接)[^。；\n]{0,40}(注销|销号)[^。；\n]{0,20}删除"),
        ]
        bad = []
        for rel in ("miniprogram/privacy.md",
                    "miniprogram/pages/privacy/privacy.wxml",
                    "miniprogram/pages/settings/settings.wxml"):
            text = re.sub(r"<!--.*?-->", " ", (REPO / rel).read_text(encoding="utf-8"),
                          flags=re.S)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text)
            for pat in pats:
                for m in pat.finditer(text):
                    win = text[max(0, m.start() - 160): m.end() + 160]
                    if ("归属" in win) or ("老报告" in win) or ("无法确认" in win):
                        continue
                    bad.append(f"{rel} 「{m.group(0)}」")
        assert bad == [], f"存在无条件的『注销即删分享链接』句（缺归属例外）：{bad}"

    def test_dao_docstring_no_longer_claims_copy_says_it(self):
        """`purge_report_files` 的 docstring 曾自称『老报告不随注销删除已写进文案』，
        而文案里没有 —— 现在两边都对得上：docstring 指明文案位置，文案确有该例外。"""
        src = (REPO / "src" / "storage" / "dao.py").read_text(encoding="utf-8")
        start = src.index("def purge_report_files")
        doc = src[start:start + 2600]
        assert "文案" in doc, "docstring 未再声明与文案的关系（要么写明实况，要么删掉该自称）"
        md = (REPO / "miniprogram" / "privacy.md").read_text(encoding="utf-8")
        assert "无法确认归属" in md, (
            "docstring 指向的文案里并没有『无法确认归属』的例外 —— 注释与文案又对不上了")


# ══════════════════════════════════════════════════════════════════════
# T2 必修2：后端用户可见文案（注销 90 天 / 数据删除）
# ══════════════════════════════════════════════════════════════════════

class TestBackendUserFacingCopy:
    def _client(self, tmp_path, monkeypatch):
        from fastapi import FastAPI
        from starlette.testclient import TestClient
        from src.api import user as user_api
        from src.security.auth import AuthHandler, set_auth_handler
        from src.storage.dao import UserDAO

        db = str(tmp_path / "users.db")
        monkeypatch.setenv("FORTUNE_DB_PATH", db)
        dao = UserDAO(db)
        ah = AuthHandler()
        user_api.setup(dao, auth_handler=ah)
        set_auth_handler(ah)
        app = FastAPI()
        app.include_router(user_api.router)
        return TestClient(app, raise_server_exceptions=False), ah, dao

    def _login(self, client, code="dev_code_k78"):
        return client.post("/api/user/login", json={"code": code})

    def test_cancel_message_and_login_403_carry_retention_exception(self, tmp_path, monkeypatch):
        from src.security.account_copy import ACCOUNT_CANCELLED_NOTICE
        client, _ah, _ = self._client(tmp_path, monkeypatch)
        tok = self._login(client).json()["token"]
        r = client.post("/api/user/cancel", json={"code": "注销"}, headers=_auth(tok))
        assert r.status_code == 200, r.text
        msg = r.json()["message"]
        assert msg == ACCOUNT_CANCELLED_NOTICE, "注销响应文案没走单一事实源"
        assert "支付流水" in msg, "注销响应缺『依法留存支付流水』例外"
        # 再登录 → 403，且 detail 与注销响应同一常量（同一事实）
        r2 = self._login(client)
        assert r2.status_code == 403, f"已注销账号登录未被拦截：{r2.status_code}"
        detail = r2.json().get("detail", "")
        assert detail == ACCOUNT_CANCELLED_NOTICE, f"登录 403 文案不是同一常量：{detail!r}"
        assert "支付流水" in detail

    def test_no_absolute_words_in_account_copy_constants(self):
        """『所有个人数据』与『不可恢复』都不得出现在这几条常量里（复审实测的两处不实）。"""
        from src.security import account_copy as ac
        for name in ("ACCOUNT_CANCELLED_NOTICE", "USER_DATA_PURGED_NOTICE",
                     "DATA_RETENTION_ACTION_NOTICE"):
            text = getattr(ac, name)
            assert "所有个人" not in text, f"{name} 又写了『所有个人数据』（不含依法留存项）"
            assert "不可恢复" not in text, (
                f"{name} 又写了『不可恢复』（与『备份仍覆盖时可人工尝试找回』冲突；"
                "本仓统一口径是『不可自助恢复』）")

    def test_main_delete_data_endpoint_message(self, tmp_path, monkeypatch):
        """`DELETE /api/user/data/{user_id}` 的响应 message 与实际一致。"""
        import src.main as main_mod
        from src.security.auth import AuthHandler, set_auth_handler
        from starlette.testclient import TestClient

        monkeypatch.setenv("FORTUNE_DB_PATH", str(tmp_path / "main.db"))
        ah = AuthHandler()
        set_auth_handler(ah)
        uid = "k78-del-me"
        client = TestClient(main_mod.app, raise_server_exceptions=False)
        r = client.delete(f"/api/user/data/{uid}", headers=_auth(ah.create_user_token(uid)))
        assert r.status_code == 200, r.text
        msg = r.json()["message"]
        from src.security.account_copy import USER_DATA_PURGED_NOTICE
        assert msg == USER_DATA_PURGED_NOTICE, f"数据删除响应文案不是单一事实源：{msg!r}"
        assert "支付流水" in msg and "所有" not in msg and "不可恢复" not in msg

    def test_security_router_delete_message_same_source(self, tmp_path, monkeypatch):
        """`DELETE /user/{user_id}/data`（security router）与上面**同一常量**。"""
        from fastapi import FastAPI
        from starlette.testclient import TestClient
        from src.security import router as sec_router
        from src.security.auth import AuthHandler, set_auth_handler
        from src.security.encryption import DataEncryptor
        from src.security.privacy import PrivacyManager

        db = str(tmp_path / "sec.db")
        monkeypatch.setenv("FORTUNE_DB_PATH", db)
        from src.storage.models import init_db       # 建表（空库会让 purge 报错）
        init_db(db).close()
        ah = AuthHandler()
        set_auth_handler(ah)
        sec_router.init_security_router(db_path=db, auth_handler=ah,
                                        encryptor=DataEncryptor())
        monkeypatch.setattr(sec_router, "_privacy_manager",
                            PrivacyManager(db, DataEncryptor()))
        app = FastAPI()
        app.include_router(sec_router.router)      # 路由自带 prefix=/api/security
        client = TestClient(app, raise_server_exceptions=False)
        uid = "k78-sec-del"
        r = client.delete(f"/api/security/user/{uid}/data",
                          headers=_auth(ah.create_user_token(uid)))
        assert r.status_code == 200, r.text
        from src.security.account_copy import USER_DATA_PURGED_NOTICE
        assert r.json()["message"] == USER_DATA_PURGED_NOTICE


# ══════════════════════════════════════════════════════════════════════
# T3 必修3：畸形 JSON → 404（不是 500）
# ══════════════════════════════════════════════════════════════════════

class TestMalformedReportIs404:
    CASES = [
        ("list", [1, 2, 3]),
        ("str", "just a string"),
        ("int", 12345),
        ("bad_profile", {"profile": [1, 2]}),
        ("bad_generated_at", {"generated_at": ["x"]}),
    ]

    def test_share_page_returns_404_not_500(self, tmp_path, monkeypatch):
        """复审实测的 5 种畸形输入：改前全 500，必须全部归入『不存在』 → 404。"""
        client, _ = _share_client(tmp_path, monkeypatch)
        bad = []
        for i, (label, payload) in enumerate(self.CASES):
            rid = "%08x" % (0xF0 + i)
            _write_report(tmp_path, rid, raw=payload)
            r = client.get(f"/share/{rid}")
            if r.status_code != 404:
                bad.append(f"{label} → {r.status_code}")
        assert bad == [], f"畸形报告分享页不是 404：{bad}"

    def test_report_json_and_page_404_on_malformed(self, tmp_path, monkeypatch):
        """/api/report/{id} 与 /report/{id} 同样把畸形内容当『不存在』（不是 500）。"""
        from fastapi import FastAPI
        from starlette.testclient import TestClient
        from src.api import visual_report as vr
        from src.security.auth import AuthHandler, set_auth_handler

        monkeypatch.setattr(vr, "_DATA_DIR", tmp_path)
        ah = AuthHandler()
        set_auth_handler(ah)
        app = FastAPI()
        app.include_router(vr.router)
        client = TestClient(app, raise_server_exceptions=False)
        h = _auth(ah.create_user_token("k78-reader"))
        bad = []
        for i, (label, payload) in enumerate(self.CASES):
            rid = "%08x" % (0xA0 + i)
            _write_report(tmp_path, rid, raw=payload)
            for path in (f"/api/report/{rid}", f"/report/{rid}"):
                r = client.get(path, headers=h)
                if r.status_code not in (403, 404):
                    bad.append(f"{label} {path} → {r.status_code}")
        assert bad == [], f"畸形报告的读接口未归入 4xx（非 403/404）：{bad}"

    def test_owner_valid_but_malformed_share_card_is_404(self, tmp_path, monkeypatch):
        """归属校验通过、内容却畸形的旧报告：分享卡片接口改前 500，应 404。"""
        from fastapi import FastAPI
        from starlette.testclient import TestClient
        from src.api import share as share_api
        from src.api.visual_report import _REPORT_OWNER_FIELD
        from src.security.auth import AuthHandler, set_auth_handler
        from src.security.encryption import DataEncryptor

        uid = "k78-card-owner"
        monkeypatch.setattr(share_api, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(share_api, "_dao", None)
        _write_report(tmp_path, "aabbcc01",
                      raw={"profile": [1, 2],
                           _REPORT_OWNER_FIELD: DataEncryptor().encrypt(uid)})
        ah = AuthHandler()
        set_auth_handler(ah)
        app = FastAPI()
        app.include_router(share_api.router)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/api/share/aabbcc01", headers=_auth(ah.create_user_token(uid)))
        assert r.status_code == 404, f"归属正确但内容畸形 → {r.status_code}（应 404，不是 500）"

    def test_render_helper_never_raises_on_malformed(self):
        """`_build_report_html` 对畸形输入**不抛异常**（改前 KeyError/TypeError）。"""
        from src.api.visual_report import _build_report_html, report_shape_problem
        for payload in ([1, 2, 3], "just a string", 12345, {"profile": [1, 2]},
                        {"generated_at": ["x"]}, {}, None):
            assert report_shape_problem(payload), f"{payload!r} 被判为可展示"
            html = _build_report_html(payload, "分享文案")
            assert isinstance(html, str) and html, "畸形输入没有返回页面"

    def test_shape_problem_is_shared_by_all_read_paths(self):
        """判据是**单一事实源**：三个读路径都调用同一个函数（不得各写一份）。"""
        share_py = (REPO / "src" / "api" / "share.py").read_text(encoding="utf-8")
        vr_py = (REPO / "src" / "api" / "visual_report.py").read_text(encoding="utf-8")
        assert share_py.count("report_shape_problem") >= 2, "分享模块未复用同一判据"
        assert vr_py.count("report_shape_problem") >= 3, "报告模块未复用同一判据"
        assert "def report_shape_problem" in vr_py


# ══════════════════════════════════════════════════════════════════════
# T4 必修4：dao=None 的 503 口径 + 过期页标题
# ══════════════════════════════════════════════════════════════════════

class TestNoneDaoAndExpiredTitle:
    def test_session_delete_503_when_dao_missing(self, tmp_path, monkeypatch):
        """session_dao=None → 503（改前 AttributeError → 500）；不多删一行。"""
        import src.main as main_mod
        from src.security.auth import AuthHandler, set_auth_handler
        from starlette.testclient import TestClient

        monkeypatch.setenv("FORTUNE_DB_PATH", str(tmp_path / "none.db"))
        ah = AuthHandler()
        set_auth_handler(ah)
        monkeypatch.setattr(main_mod, "session_dao", None)
        client = TestClient(main_mod.app, raise_server_exceptions=False)
        h = _auth(ah.create_user_token("k78-nodaoo"))
        r = client.post("/api/chat/sessions/delete", json={"scope": "legacy"}, headers=h)
        assert r.status_code == 503, f"dao 缺失时删除对话返回 {r.status_code}（应 503）"
        assert "Service not ready" in r.json().get("detail", "")

    def test_pending_endpoints_are_not_affected(self, tmp_path, monkeypatch):
        """对照：读接口 dao=None 仍按『无补全』降级（200），本批**未**改它们的口径。"""
        import src.main as main_mod
        from src.security.auth import AuthHandler, set_auth_handler
        from starlette.testclient import TestClient

        monkeypatch.setenv("FORTUNE_DB_PATH", str(tmp_path / "none2.db"))
        ah = AuthHandler()
        set_auth_handler(ah)
        monkeypatch.setattr(main_mod, "session_dao", None)
        client = TestClient(main_mod.app, raise_server_exceptions=False)
        h = _auth(ah.create_user_token("k78-nodaoo2"))
        r = client.get("/api/chat/pending?session_id=s_aaaa1111", headers=h)
        assert r.status_code == 200 and r.json() == {"items": []}

    def test_report_expired_page_title_is_accurate(self, tmp_path, monkeypatch):
        """报告过期页的 <title> 必须是『报告分享已过期』，不能再顶着对话分享的标题。"""
        import re as _re
        client, _ = _share_client(tmp_path, monkeypatch)
        _write_report(tmp_path, "deadbeef", age_days=40)
        r = client.get("/share/deadbeef")
        assert r.status_code == 410
        m = _re.search(r"<title>(.*?)</title>", r.text)
        assert m, "过期页没有 <title>"
        assert m.group(1) == "报告分享已过期 · 易理明灯", f"过期页标题不准确：{m.group(1)!r}"
        assert "一段对话" not in m.group(1)

    def test_share_pages_keep_their_own_titles(self, tmp_path, monkeypatch):
        """对话分享页/报告分享页的标题各自准确（标题不再由 HEAD 常量一处写死）。"""
        import re as _re
        client, _ = _share_client(tmp_path, monkeypatch)
        _write_report(tmp_path, "aaaa0001")            # 未过期 → 200 正常报告页
        r = client.get("/share/aaaa0001")
        assert r.status_code == 200
        assert _re.search(r"<title>我的2026运势报告", r.text), "报告分享页标题不对"
        from src.api import share as share_api
        html = share_api._render_share_page("abcd1234", {"pairs": []})
        assert "<title>易理明灯 · 一段对话</title>" in html, "对话分享页标题变了"
        assert "__TITLE__" not in share_api._share_expired_html(), "过期页留了未替换的标题占位符"
        assert "__TITLE__" not in html
