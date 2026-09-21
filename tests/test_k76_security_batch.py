"""k76 安全批次回归门禁 —— k72 独立核查"报出来但停手上报"的 6 项，控制方裁定必修。

覆盖（每条对应一批改动，防回退）：
  T1 分享链接有效期：默认 30 天可配 / 到期 410 且不返回内容 / 存量按创建时间回算 /
     过期行物理清理 / 注销时删除本人分享（且不误删他人的）
  T2 /share/{reading_id} 公开页剥离个人信息（姓名、出生日期）
  T3 /api/report/{id} 与 /report/{id} 归属校验（无令牌 401 / 非本人 403 / 本人 200）
  T4 注销删除覆盖：清单单一事实源 + 逐表清干净 + 支付流水依法留存 + 文件类落点
  T5 /api/face-reading & /api/palm-reading：大小上限与 chat/upload 同源 + 临时文件
     **异常路径也不泄漏**
  T6 死代码外发路径 compare_with_wenzhen 已移除（且无动态调用残留）

红线：不触网、不开生产库（库路径一律 tmp_path / FORTUNE_DB_PATH 沙箱）。
"""
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── T1 分享有效期 ─────────────────────────────────────────────────────

class TestShareTTL:
    def _dao(self, tmp_path):
        import sqlite3
        from src.storage.share_dao import ShareDAO
        conn = sqlite3.connect(str(tmp_path / "share.db"))
        return ShareDAO(conn), conn

    def test_default_30_days_and_env_override(self, monkeypatch):
        from src.config import (SHARE_TTL_DAYS_ENV, SHARE_TTL_DAYS_DEFAULT,
                                share_ttl_days, share_ttl_seconds)
        monkeypatch.delenv(SHARE_TTL_DAYS_ENV, raising=False)
        assert SHARE_TTL_DAYS_DEFAULT == 30
        assert share_ttl_days() == 30.0
        assert share_ttl_seconds() == 30 * 86400.0
        monkeypatch.setenv(SHARE_TTL_DAYS_ENV, "7")
        assert share_ttl_days() == 7.0
        # 非法值 / 0 / 负数一律回落默认（不得被解释为"永不过期"）
        for bad in ("abc", "0", "-3", ""):
            monkeypatch.setenv(SHARE_TTL_DAYS_ENV, bad)
            assert share_ttl_days() == 30.0, bad

    def test_expires_at_is_created_plus_ttl(self, tmp_path):
        dao, _ = self._dao(tmp_path)
        assert dao.insert("AAAAAAAA", {"pairs": []})
        created, expires = dao.conn.execute(
            "SELECT created_at, expires_at FROM share_entries WHERE id='AAAAAAAA'"
        ).fetchone()
        assert expires - created == pytest.approx(30 * 86400.0, abs=2)

    def test_expired_is_not_readable_and_state_is_distinct(self, tmp_path):
        dao, conn = self._dao(tmp_path)
        dao.insert("EXPIRED1", {"pairs": [{"u": "", "tag": "", "content": "过期正文"}]})
        dao.insert("ALIVE001", {"pairs": [{"u": "", "tag": "", "content": "有效正文"}]})
        conn.execute("UPDATE share_entries SET expires_at=? WHERE id='EXPIRED1'",
                     (time.time() - 1,))
        conn.commit()
        assert dao.get_state("EXPIRED1")[0] == "expired"
        assert dao.get("EXPIRED1") is None            # 过期绝不返回内容（fail-closed）
        assert dao.get_state("ALIVE001")[0] == "ok"
        assert dao.get_state("MISSING1")[0] == "missing"
        assert dao.purge_expired() == 1
        assert dao.get_state("EXPIRED1")[0] == "missing"

    def test_legacy_rows_backfilled_from_created_at(self, tmp_path):
        """存量行：按 created_at 回算 —— 已超期的一并失效（控制方："已有链接也会失效"）。"""
        import sqlite3
        from src.storage.share_dao import ShareDAO
        p = tmp_path / "legacy.db"
        c = sqlite3.connect(str(p))
        c.execute("CREATE TABLE share_entries (id TEXT PRIMARY KEY, content TEXT NOT NULL,"
                  " created_at REAL)")
        c.execute("INSERT INTO share_entries VALUES ('OLDOLD01','{}',?)",
                  (time.time() - 40 * 86400,))
        c.execute("INSERT INTO share_entries VALUES ('NEWNEW01','{}',?)",
                  (time.time() - 2 * 86400,))
        c.commit()
        dao = ShareDAO(c)                     # 构造即迁移
        cols = {r[1] for r in c.execute("PRAGMA table_info(share_entries)")}
        assert {"expires_at", "owner_tag"} <= cols
        assert dao.get_state("OLDOLD01")[0] == "expired"
        assert dao.get_state("NEWNEW01")[0] == "ok"
        ShareDAO(c)                            # 幂等：二次构造不改结论
        assert dao.get_state("NEWNEW01")[0] == "ok"

    def test_null_expires_at_treated_as_expired(self, tmp_path):
        """expires_at 为 NULL（理论上不该有）→ 按过期处理，绝不退回"永久有效"。"""
        dao, conn = self._dao(tmp_path)
        dao.insert("NULLEXP1", {"pairs": []})
        conn.execute("UPDATE share_entries SET expires_at=NULL WHERE id='NULLEXP1'")
        conn.commit()
        assert dao.get_state("NULLEXP1") == ("expired", None)

    def test_owner_tag_is_pseudonymous_and_scoped(self, tmp_path):
        from src.storage.share_dao import owner_tag_for
        uid = "user-abc"
        tag = owner_tag_for(uid)
        assert tag and uid not in tag
        assert tag == owner_tag_for(uid)                 # 确定性
        assert owner_tag_for("") == ""
        dao, conn = self._dao(tmp_path)
        dao.insert("MINE0001", {"pairs": []}, owner_tag=tag)
        dao.insert("OTHER001", {"pairs": []}, owner_tag=owner_tag_for("someone-else"))
        dao.insert("ANON0001", {"pairs": []})            # 匿名创建：无归属
        assert dao.delete_by_owner(tag) == 1
        assert dao.get_state("MINE0001")[0] == "missing"
        assert dao.get_state("OTHER001")[0] == "ok"      # 不误删他人
        assert dao.get_state("ANON0001")[0] == "ok"
        assert dao.delete_by_owner("") == 0              # 空标记绝不触发全表删除

    def test_http_expired_returns_410_missing_returns_200(self, tmp_path, monkeypatch):
        """到期行为**明确**：410 Gone + 「已过期」；不存在仍是既有 200 空页。"""
        from fastapi import FastAPI
        from starlette.testclient import TestClient
        from src.api import share as share_api
        monkeypatch.setenv("FORTUNE_DB_PATH", str(tmp_path / "api.db"))
        monkeypatch.setattr(share_api, "_share_dao", None)
        app = FastAPI()
        app.include_router(share_api.router)
        client = TestClient(app)
        r = client.post("/api/share", json={"pairs": [{"u": "问", "tag": "t",
                                                       "content": "答案正文"}],
                                            "dateText": "d"})
        assert r.status_code == 200
        sid = r.json()["id"]
        assert client.get(f"/share?id={sid}").status_code == 200
        assert "答案正文" in client.get(f"/share?id={sid}").text
        conn = share_api._sdao().conn
        conn.execute("UPDATE share_entries SET expires_at=? WHERE id=?",
                     (time.time() - 1, sid))
        conn.commit()
        r = client.get(f"/share?id={sid}")
        assert r.status_code == 410
        assert "过期" in r.text
        assert "答案正文" not in r.text                  # 过期后绝不吐内容
        r2 = client.get("/share?id=ZZZZZZZZ")
        assert r2.status_code == 200 and "随风而去" in r2.text

    def test_cancel_deletes_own_shares_only(self, tmp_path, monkeypatch):
        """注销时一并删除本人分享（控制方 T1），且不碰他人 / 匿名创建的。"""
        from src.storage.dao import UserDAO, get_conn
        from src.storage.share_dao import ShareDAO, owner_tag_for
        db = str(tmp_path / "cancel.db")
        monkeypatch.setenv("FORTUNE_DB_PATH", db)
        import src.storage.dao as dao_mod
        monkeypatch.setattr(dao_mod, "_DB_PATH", db, raising=False)
        dao = UserDAO(db)
        sdao = ShareDAO(get_conn())
        me, other = "k76-cancel-me", "k76-cancel-other"
        sdao.insert("MINE0001", {"pairs": []}, owner_tag=owner_tag_for(me))
        sdao.insert("OTHER001", {"pairs": []}, owner_tag=owner_tag_for(other))
        sdao.insert("ANON0001", {"pairs": []})
        dao.cancel_user(me)
        assert sdao.get_state("MINE0001")[0] == "missing"
        assert sdao.get_state("OTHER001")[0] == "ok"
        assert sdao.get_state("ANON0001")[0] == "ok"
        assert dao.get_user_status(me) == "cancelled"


# ── T2/T3 分享页个人信息 + 报告归属 ────────────────────────────────────

class TestSharePageRedaction:
    def test_redact_strips_name_and_birth_date(self):
        from src.api.share import _redact_report_for_share
        report = {"reading_id": "r1",
                  "profile": {"name": "张三", "bazi": "庚午 辛巳 乙酉 甲申",
                              "birth_date": "1990年5月20日",
                              "birth_info": "1990年5月20日", "gender": "男"},
                  "insights": ["运势不错"]}
        safe = _redact_report_for_share(report)
        assert safe["profile"]["name"] == "用户"
        assert safe["profile"]["birth_date"] == ""
        assert safe["profile"]["birth_info"] == ""
        assert "张三" not in str(safe)
        assert "1990年5月20日" not in str(safe)
        assert safe["profile"]["bazi"] == "庚午 辛巳 乙酉 甲申"      # 分享内容本体保留
        assert report["profile"]["name"] == "张三"                   # 不改原 dict

    def test_redacted_page_carries_no_name(self):
        from src.api.visual_report import _build_report_html
        from src.api.share import _redact_report_for_share
        report = {"reading_id": "r2", "generated_at": "", "generated_date": "",
                  "version": "v5.0",
                  "profile": {"name": "李四", "bazi": "甲子 乙丑 丙寅 丁卯",
                              "day_master": "甲木", "gender": "女",
                              "birth_date": "1988年8月8日", "birth_info": "1988年8月8日"},
                  "bazi_analysis": {"wuxing": {"金": 1}, "shishen": [], "geju": "x",
                                    "yongshen": "x", "shensha": [], "nayin": [],
                                    "dayun": [], "liunian": {}},
                  "charts": {"monthly_fortune": [], "annual_trend": [],
                             "wuxing_radar": []},
                  "insights": ["洞察"], "recommendations": ["建议"]}
        html = _build_report_html(_redact_report_for_share(report), "文案")
        assert "李四" not in html
        assert "1988年8月8日" not in html
        assert "我的2026运势报告" in html


class TestReportOwnership:
    def _client(self, tmp_path, monkeypatch):
        from fastapi import FastAPI
        from starlette.testclient import TestClient
        from src.api import visual_report as vr
        from src.security.auth import AuthHandler, set_auth_handler
        monkeypatch.setattr(vr, "_DATA_DIR", tmp_path)
        ah = AuthHandler()
        set_auth_handler(ah)
        app = FastAPI()
        app.include_router(vr.router)
        return TestClient(app), ah, vr

    def _write_report(self, tmp_path, reading_id, owner_uid=None):
        import json
        from src.api.visual_report import _REPORT_OWNER_FIELD
        data = {"reading_id": reading_id,
                "profile": {"name": "王五", "bazi": "甲子 乙丑 丙寅 丁卯",
                            "birth_date": "1991年1月1日"},
                "insights": ["洞察"], "bazi_analysis": {}, "charts": {}}
        if owner_uid:
            from src.security.encryption import DataEncryptor
            data[_REPORT_OWNER_FIELD] = DataEncryptor().encrypt(owner_uid)
        (tmp_path / f"{reading_id}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_json_requires_auth(self, tmp_path, monkeypatch):
        client, ah, _ = self._client(tmp_path, monkeypatch)
        self._write_report(tmp_path, "owned001", "u-owner")
        assert client.get("/api/report/owned001").status_code == 401
        assert client.get(
            "/api/report/owned001",
            headers={"Authorization": f"Bearer {ah.create_user_token('u-other')}"}
        ).status_code == 403
        r = client.get("/api/report/owned001",
                       headers={"Authorization": f"Bearer {ah.create_user_token('u-owner')}"})
        assert r.status_code == 200 and r.json()["reading_id"] == "owned001"

    def test_html_page_same_rules(self, tmp_path, monkeypatch):
        client, ah, _ = self._client(tmp_path, monkeypatch)
        self._write_report(tmp_path, "owned002", "u-owner")
        assert client.get("/report/owned002").status_code == 401
        assert client.get(
            "/report/owned002",
            headers={"Authorization": f"Bearer {ah.create_user_token('u-other')}"}
        ).status_code == 403
        r = client.get("/report/owned002",
                       headers={"Authorization": f"Bearer {ah.create_user_token('u-owner')}"})
        assert r.status_code == 200 and "王五" in r.text

    def test_legacy_report_without_owner_is_denied(self, tmp_path, monkeypatch):
        """归属未知的老报告 fail-closed：不给任何登录用户（含"本人"）。"""
        client, ah, _ = self._client(tmp_path, monkeypatch)
        self._write_report(tmp_path, "legacy01")           # 无 owner_enc
        for uid in ("u-owner", "u-other"):
            r = client.get("/api/report/legacy01",
                           headers={"Authorization": f"Bearer {ah.create_user_token(uid)}"})
            assert r.status_code == 403, uid

    def test_missing_report_still_404(self, tmp_path, monkeypatch):
        client, ah, _ = self._client(tmp_path, monkeypatch)
        r = client.get("/api/report/nosuch01",
                       headers={"Authorization": f"Bearer {ah.create_user_token('u-x')}"})
        assert r.status_code == 404

    def test_generate_records_owner(self, tmp_path, monkeypatch):
        from src.api.visual_report import generate_report_data, report_owner_tag
        from src.engines.bazi import BaziEngine
        import src.api.visual_report as vr
        monkeypatch.setattr(vr, "_DATA_DIR", tmp_path)
        res = BaziEngine().calculate(1990, 5, 20, 10, 30, "北京", "男")
        rep = generate_report_data(res, birth={"year": 1990, "month": 5, "day": 20,
                                               "gender": "男", "name": "赵六"},
                                   name="赵六", owner_uid="u-owner")
        assert report_owner_tag(rep) == "u-owner"
        assert "u-owner" not in str(rep.get("owner_enc"))   # 落盘是密文
        # 不传 owner_uid → 归属未知（老调用方语义保持）
        rep2 = generate_report_data(res, birth=None, name="")
        assert report_owner_tag(rep2) == ""


# ── T4 注销删除覆盖 ───────────────────────────────────────────────────

class TestAccountPurgeCoverage:
    def test_inventory_covers_every_user_scoped_table(self, tmp_path, monkeypatch):
        """清单必须覆盖库里所有"带用户归属列"的表（新增用户表必须登记）。"""
        from src.storage.dao import UserDAO, get_conn
        from src.storage.models import ACCOUNT_PURGE_TABLES, ACCOUNT_RETAIN_TABLES
        import src.storage.dao as dao_mod
        from src.storage.session_dao import SessionDAO
        from src.storage.member_dao import MemberDAO
        from src.storage.person_dao import PersonDAO
        from src.storage.chart_dao import ChartDAO
        from src.storage.favorite_dao import FavoriteDAO
        from src.storage.chat_quota_dao import ChatQuotaDAO
        from src.storage.jian_dao import JianPrefDAO
        from src.storage.ming_dao import MingDAO
        from src.storage.qian_dao import QianDAO
        from src.storage.zeri_dao import ZeriDAO
        from src.storage.night_dao import NightPrefDAO
        from src.storage.lamp_dao import LampDAO
        from src.storage.share_dao import ShareDAO

        db = str(tmp_path / "inv.db")
        # 库路径必须**显式注入**：轻量 DAO 走 dao._DB_PATH，否则会落到
        # FORTUNE_DB_PATH 沙箱（那里是生产快照，跑测试不该往它建表）
        monkeypatch.setattr(dao_mod, "_DB_PATH", db)
        UserDAO(db)
        for cls in (SessionDAO, MemberDAO, PersonDAO, ChartDAO, FavoriteDAO,
                    ChatQuotaDAO):
            cls(db)
        conn = dao_mod.get_conn()
        for factory in (JianPrefDAO, MingDAO, QianDAO, ZeriDAO, NightPrefDAO,
                        LampDAO, ShareDAO):
            factory(conn)
        listed = {t for t, _ in ACCOUNT_PURGE_TABLES} | {t for t, _c, _w in ACCOUNT_RETAIN_TABLES}
        # users 主表由 purge_account_data 显式删除（放最后），不在元组清单里
        listed |= {"users"}
        found = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        # 逐个真实用户表都必须在清单里（sqlite_sequence 是内部表）
        user_tables = set()
        for t in found - {"sqlite_sequence"}:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
            if {"user_id", "owner_tag"} & cols:
                user_tables.add(t)
        assert user_tables, "测试库没建出用户表，用例失效"
        assert {"sessions", "persons", "share_entries", "zeri_plans",
                "night_lamp"} <= user_tables, "用例失效：关键表没建出来"
        missing = sorted(user_tables - listed)
        assert missing == [], f"未登记进删除清单的用户表: {missing}"

    def test_purge_clears_rows_and_keeps_payment_records(self, tmp_path, monkeypatch):
        from src.storage.models import ACCOUNT_PURGE_TABLES, ACCOUNT_RETAIN_TABLES
        from src.storage.dao import UserDAO, purge_account_data
        import src.storage.dao as dao_mod
        db = str(tmp_path / "purge.db")
        dao = UserDAO(db)
        conn = dao_mod.get_conn()
        me = "k76-purge-me"
        now = "2026-09-21T10:00:00"
        conn.execute("INSERT INTO users (user_id, created_at, updated_at) VALUES (?,?,?)",
                     (me, now, now))
        rows = {
            "consultations": "INSERT INTO consultations (user_id, question) VALUES (?,?)",
            "push_log": "INSERT INTO push_log (user_id, push_date) VALUES (?,?)",
            "memberships": "INSERT INTO memberships (user_id, plan) VALUES (?,?)",
            "payments": "INSERT INTO payments (user_id, amount, plan) VALUES (?,?,?)",
            "user_preferences": "INSERT INTO user_preferences (user_id) VALUES (?)",
            "sessions": "INSERT INTO sessions (user_id, role, content) VALUES (?,?,?)",
            "session_summaries": "INSERT INTO session_summaries (user_id) VALUES (?)",
            "persons": "INSERT INTO persons (user_id, name) VALUES (?,?)",
        }
        args = {"consultations": (me, "q"), "push_log": (me, "2026-09-21"),
                "memberships": (me, "pro"), "payments": (me, 1.0, "pro"),
                "user_preferences": (me,), "sessions": (me, "user", "c"),
                "session_summaries": (me,), "persons": (me, "本人")}
        for t, sql in rows.items():
            conn.execute(sql, args[t])
        # 遗留表（生产库确实存在、代码零引用）也要能被删干净
        conn.execute("CREATE TABLE IF NOT EXISTS user_tone_feedback ("
                     "user_id TEXT NOT NULL, tone TEXT NOT NULL, count INTEGER,"
                     " updated_at TEXT, PRIMARY KEY (user_id, tone))")
        conn.execute("INSERT INTO user_tone_feedback (user_id, tone, count) VALUES (?,?,?)",
                     (me, "gentle", 3))
        conn.commit()

        stats = purge_account_data(conn, me)
        conn.commit()
        for table, key in ACCOUNT_PURGE_TABLES:
            if table not in rows and table != "user_tone_feedback":
                continue
            n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {key}=?",
                             (me,)).fetchone()[0]
            assert n == 0, f"{table} 残留 {n} 行"
        assert stats["tables"]["user_tone_feedback"] == 1     # 遗留表那 1 行确实被删
        # 依法留存：支付流水必须还在
        assert conn.execute("SELECT COUNT(*) FROM payments WHERE user_id=?",
                            (me,)).fetchone()[0] == 1
        for t, _c, why in ACCOUNT_RETAIN_TABLES:
            assert why, f"{t} 必须写明留存依据"
        assert stats["retained"]

    def test_purge_deletes_files_and_only_referenced_uploads(self, tmp_path, monkeypatch):
        import src.storage.dao as dao_mod
        from src.storage.dao import UserDAO, purge_account_data
        from src.storage.share_dao import ShareDAO, owner_tag_for
        db = str(tmp_path / "files.db")
        avatar_dir = tmp_path / "avatars"
        uploads = tmp_path / "uploads"
        mem = tmp_path / "memory"
        for d in (avatar_dir, uploads, mem):
            d.mkdir(parents=True, exist_ok=True)
        me, other = "k76-files-me", "k76-other"
        monkeypatch.setattr(dao_mod, "_DB_PATH", db)
        UserDAO(db)
        conn = dao_mod.get_conn()
        now = "2026-09-21T10:00:00"
        conn.execute("INSERT INTO users (user_id, created_at, updated_at) VALUES (?,?,?)",
                     (me, now, now))
        conn.execute("INSERT INTO sessions (user_id, role, content) VALUES (?,?,?)",
                     (me, "user", "看图 /api/chat/uploads/mine.jpg"))
        conn.commit()
        (uploads / "mine.jpg").write_bytes(b"x")
        (uploads / "others.jpg").write_bytes(b"x")
        (avatar_dir / f"{me}.jpg").write_bytes(b"x")
        (avatar_dir / f"{other}.jpg").write_bytes(b"x")
        (mem / f"{me}.json").write_text("{}", encoding="utf-8")

        # "." / ".." 不是文件名，采集时必须剔除（删除面不交给巧合）
        conn.execute("INSERT INTO sessions (user_id, role, content) VALUES (?,?,?)",
                     (me, "user", "http://x/api/chat/uploads/.. 与 /api/chat/uploads/."))
        conn.commit()
        names = dao_mod.collect_user_upload_names(conn, me, owner_tag_for(me))
        assert names == {"mine.jpg"}
        stats = purge_account_data(conn, me, memory_dir=str(mem),
                                   avatar_dir=str(avatar_dir),
                                   uploads_dir=str(uploads))
        conn.commit()
        assert not (uploads / "mine.jpg").exists()
        assert (uploads / "others.jpg").exists()          # 别人的图不能误删
        assert not (avatar_dir / f"{me}.jpg").exists()
        assert (avatar_dir / f"{other}.jpg").exists()
        assert not (mem / f"{me}.json").exists()
        assert stats["files"]["uploads"] == 1


# ── T5 面相/手相上传 ──────────────────────────────────────────────────

class TestFacePalmUpload:
    def _env(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        import src.main as main_mod
        from src.security.auth import AuthHandler, set_auth_handler
        tmpdir = tmp_path / "tmpfiles"
        tmpdir.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(tmpdir))
        monkeypatch.setenv("FORTUNE_UPLOADS_DIR", str(tmp_path / "uploads"))

        class _Handler:
            llm = None
            retriever = None

        # handler 非空：否则两个端点先走 503（服务未就绪）分支，测不到上传校验
        monkeypatch.setattr(main_mod, "handler", _Handler())
        ah = AuthHandler()
        set_auth_handler(ah)
        client = TestClient(main_mod.app)
        headers = {"Authorization": f"Bearer {ah.create_user_token('k76-u')}"}
        return client, headers, main_mod, tmpdir

    def test_size_limit_shares_single_source_with_chat_upload(self, tmp_path, monkeypatch):
        client, headers, main_mod, _ = self._env(tmp_path, monkeypatch)
        max_bytes = main_mod._CHAT_UPLOAD_MAX
        assert max_bytes == 5 * 1024 * 1024
        import inspect
        for fn in (main_mod.face_reading, main_mod.palm_reading):
            src = inspect.getsource(fn)
            assert "_read_upload_within_limit" in src
        assert "_CHAT_UPLOAD_MAX" in inspect.getsource(
            main_mod._read_upload_within_limit)
        over = b"\xff\xd8\xff\xe0" + b"\x00" * max_bytes
        for path, field in (("/api/face-reading", "image"),
                            ("/api/palm-reading", "image"),
                            ("/api/chat/upload", "file")):
            r = client.post(path, files={field: ("a.jpg", over, "image/jpeg")},
                            headers=headers)
            assert r.status_code == 413, (path, r.status_code)
            assert "5MB" in r.text

    def test_temp_file_removed_on_error_path(self, tmp_path, monkeypatch):
        """analyze() 抛错（k72 遗留风险）也必须清理临时文件。"""
        client, headers, main_mod, tmpdir = self._env(tmp_path, monkeypatch)
        import src.engines.face_reader as fr
        import src.engines.palm_reader as pr

        class _Boom:
            def analyze(self, path):
                assert os.path.exists(path)          # 分析期间文件确实在
                raise RuntimeError("boom")

        class _Handler:
            llm = None
            retriever = None

        monkeypatch.setattr(main_mod, "handler", _Handler())
        monkeypatch.setattr(fr, "FaceReader", _Boom)
        monkeypatch.setattr(pr, "PalmReader", _Boom)
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
        for path in ("/api/face-reading", "/api/palm-reading"):
            r = client.post(path, files={"image": ("a.jpg", jpeg, "image/jpeg")},
                            headers=headers)
            assert r.status_code == 200 and r.json()["status"] == "error"
        assert list(tmpdir.iterdir()) == []          # 零泄漏

    def test_requires_login(self, tmp_path, monkeypatch):
        client, _headers, _m, _t = self._env(tmp_path, monkeypatch)
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
        for path in ("/api/face-reading", "/api/palm-reading"):
            assert client.post(path, files={"image": ("a.jpg", jpeg,
                                                      "image/jpeg")}).status_code == 401


# ── T6 死代码外发路径已移除 ────────────────────────────────────────────

class TestNoWenzhenEgress:
    def test_dead_exfil_function_is_gone(self):
        import src.engines.bazi_formatter as bf
        assert not hasattr(bf, "compare_with_wenzhen")

    def test_no_source_reference_anywhere(self):
        """`src/` 里不得再出现该函数名/该外发域名（只允许出现在**注释**里。

        注释里的删除说明是**故意留**的（防止后人误以为漏删又加回来）；判定"还有
        调用/还有外发 URL"看的是非注释代码行。
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src_root = os.path.join(root, "src")
        hits = []
        for dirpath, _dirs, files in os.walk(src_root):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath, fn)
                with open(p, encoding="utf-8") as fh:
                    for i, line in enumerate(fh, 1):
                        code = line.split("#", 1)[0]
                        if "compare_with_wenzhen" in code or "iwzbz" in code:
                            hits.append(f"{p}:{i}:{line.rstrip()}")
        assert hits == [], hits
