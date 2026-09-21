"""k77 隐私/安全收口批回归门禁 —— 与 k75-reconcile 的合成体复审结论逐条对应。

覆盖（每条对应一处改动，防回退）：
  T1 报告分享页 /share/{reading_id} 的**有效期**（k77-F）：与对话分享**同一配置口径**
     （src/config.py::share_ttl_days 单一事实源）；过期 410 且不吐内容；存量回算
     （无 generated_at → 文件 mtime）；两条都拿不到 → fail-closed 视为已过期；
     reading_id 形态校验；**并钉住 k76 的姓名剥离没有被改回去**。
  T2 注销的「公开面立即消失」（k77-F 联动）：注销当时就删本人报告文件与分享图，
     不动他人 / 不动无归属的老报告；注销后该分享页 404。
  T3 「删除对话」接通服务端（k77-I4）：session/legacy 两个作用域真删 sessions 行；
     归属严格限定本人；作用域缺失/非法 → 400；未登录 → 401。
  T4 已注销账号不再被推送触达（k77）：三条触达链共用的过滤点 + pushable 查询。

红线：不触网、不开生产库（库路径一律 tmp_path；报告目录 monkeypatch 到 tmp）。
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = Path(__file__).resolve().parent.parent


def _report_payload(reading_id: str, name: str = "张三") -> dict:
    """最小可用报告（字段形状与生产 data/reports/*.json 一致）。"""
    return {
        "reading_id": reading_id,
        "generated_at": "",
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


def _write_report(dirpath: Path, reading_id: str, *, age_days=None,
                  with_generated_at=True, name="张三", owner_uid=None) -> Path:
    """写一份报告文件。

    age_days：让"生成时刻"回溯这么多天 —— 同时作用于 `generated_at`（若有）与
    文件 mtime（老报告回算用的就是 mtime）。with_generated_at=False 模拟
    **没有生成时刻元数据的老报告**（只剩 mtime 可回算）。
    """
    data = _report_payload(reading_id, name=name)
    if not with_generated_at:
        data.pop("generated_at", None)
    if owner_uid:
        from src.api.visual_report import _REPORT_OWNER_FIELD
        from src.security.encryption import DataEncryptor
        data[_REPORT_OWNER_FIELD] = DataEncryptor().encrypt(owner_uid)
    if with_generated_at and age_days is not None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S",
                              time.localtime(time.time() - age_days * 86400))
        data["generated_at"] = stamp
    p = dirpath / f"{reading_id}.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if age_days is not None:
        ts = time.time() - age_days * 86400
        os.utime(p, (ts, ts))
    return p


def _report_client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from src.api import share as share_api
    monkeypatch.setattr(share_api, "_DATA_DIR", tmp_path)
    app = FastAPI()
    app.include_router(share_api.router)
    return TestClient(app), share_api


# ── T1 报告分享页有效期（k77-F）────────────────────────────────────────

class TestReportShareTTL:
    def test_live_report_served_and_name_stripped(self, tmp_path, monkeypatch):
        """未过期 → 200 + 报告内容；且 k76 的姓名剥离**不得回退**。"""
        client, _ = _report_client(tmp_path, monkeypatch)
        _write_report(tmp_path, "aabbcc01", age_days=1, name="张三")
        r = client.get("/share/aabbcc01")
        assert r.status_code == 200
        assert "洞察内容" in r.text                 # 报告本体照旧可读
        assert "张三" not in r.text                 # 姓名必须被剥离（k76 口径）
        assert "1990年5月20日" not in r.text        # 出生日期同样不得出现

    def test_expired_report_is_410_without_content(self, tmp_path, monkeypatch):
        """超过 30 天（按 generated_at 判定）→ 410 且**不返回任何报告内容**。"""
        client, _ = _report_client(tmp_path, monkeypatch)
        _write_report(tmp_path, "aabbcc02", age_days=40)
        r = client.get("/share/aabbcc02")
        assert r.status_code == 410
        assert "过期" in r.text
        assert "洞察内容" not in r.text             # fail-closed：过期不吐内容

    def test_generated_at_wins_over_mtime(self, tmp_path, monkeypatch):
        """有生成时刻时**以生成时刻为准**（mtime 会因复制/同步而虚高）。

        构造：文件刚落地（mtime=现在）但报告 40 天前生成 → 必须判过期。
        """
        client, _ = _report_client(tmp_path, monkeypatch)
        p = _write_report(tmp_path, "aabbcc07", age_days=40)
        os.utime(p, None)                            # 把 mtime 抹成"刚刚"
        assert client.get("/share/aabbcc07").status_code == 410

    def test_legacy_report_without_generated_at_backfilled_by_mtime(self, tmp_path, monkeypatch):
        """存量回算：老报告没有 generated_at → 用文件 mtime 回算。

        与对话分享的存量回算同款（share_dao 对老行按 created_at 回算），
        口径一致：**已经超期的一并失效**，未超期的照常可看。
        """
        client, _ = _report_client(tmp_path, monkeypatch)
        _write_report(tmp_path, "aabbcc03", with_generated_at=False, age_days=1)
        assert client.get("/share/aabbcc03").status_code == 200      # 未超期
        _write_report(tmp_path, "aabbcc04", with_generated_at=False, age_days=45)
        assert client.get("/share/aabbcc04").status_code == 410      # 超期 → 失效

    def test_expiry_helper_fails_closed_when_undecidable(self, tmp_path):
        """两条路都拿不到（文件已消失且无 generated_at）→ 视为**已过期**，
        绝不因为"算不出来"就退回"永久有效"（与 ShareDAO.get_state 同款 fail-closed）。"""
        from src.api.share import _report_share_expires_at
        assert _report_share_expires_at({}, tmp_path / "not-there.json") == 0.0
        assert _report_share_expires_at({"generated_at": "不是时间"}, tmp_path / "nope.json") == 0.0

    def test_same_config_source_as_conversation_share(self, tmp_path, monkeypatch):
        """**与对话分享同一配置口径**：改 FORTUNE_SHARE_TTL_DAYS → 两个入口同时变。

        这是 k77-F 的选择（不另立第二旋钮）：一条口径只有一个取值，杜绝
        "对话 30 天 / 报告永久"这类半失效状态。
        """
        from src.config import SHARE_TTL_DAYS_ENV
        client, _ = _report_client(tmp_path, monkeypatch)
        monkeypatch.setenv(SHARE_TTL_DAYS_ENV, "7")
        _write_report(tmp_path, "aabbcc05", age_days=6)
        _write_report(tmp_path, "aabbcc06", age_days=8)
        assert client.get("/share/aabbcc05").status_code == 200
        assert client.get("/share/aabbcc06").status_code == 410

    def test_reading_id_shape_is_validated(self, tmp_path, monkeypatch):
        """形态非法 → 404（匿名入口的纵深防御：不做磁盘探测、不接受怪串）。"""
        client, _ = _report_client(tmp_path, monkeypatch)
        for bad in ("..", "aabbcc", "AABBCC01", "aabbcc01x", "%2e%2e%2fetc"):
            assert client.get(f"/share/{bad}").status_code == 404, bad
        # 合法形态但不存在 → 404（既有行为）
        assert client.get("/share/ffffffff").status_code == 404


# ── T2 注销：公开面立即消失（k77-F 联动）──────────────────────────────

class TestCancelRemovesPublicReportFace:
    def test_cancel_deletes_own_report_files_only(self, tmp_path, monkeypatch):
        from src.storage.dao import UserDAO
        me, other = "k77-cancel-me", "k77-cancel-other"
        rep_dir = tmp_path / "reports"
        rep_dir.mkdir()
        charts = tmp_path / "charts"
        charts.mkdir()
        monkeypatch.setenv("FORTUNE_DB_PATH", str(tmp_path / "cancel.db"))
        import src.storage.dao as dao_mod
        monkeypatch.setattr(dao_mod, "_DB_PATH", str(tmp_path / "cancel.db"), raising=False)
        # 三份报告：本人（有归属）/ 他人（有归属）/ 老报告（无归属字段）
        mine = _write_report(rep_dir, "aaaa1111", owner_uid=me)
        theirs = _write_report(rep_dir, "bbbb2222", owner_uid=other)
        legacy = _write_report(rep_dir, "cccc3333")
        (charts / "share_aaaa1111.png").write_bytes(b"png")
        (charts / "share_bbbb2222.png").write_bytes(b"png")
        dao = UserDAO(str(tmp_path / "cancel.db"))
        import src.storage.dao as dm
        stats = dm.purge_report_files(me, reports_dir=str(rep_dir), charts_dir=str(charts))
        assert stats["reports"] == 1 and stats["share_cards"] == 1
        assert not mine.exists()
        assert theirs.exists(), "误删了他人的报告文件"
        assert legacy.exists(), "无归属字段的老报告不应删（少删不误删）"
        assert (charts / "share_aaaa1111.png").exists() is False
        assert (charts / "share_bbbb2222.png").exists()

    def test_cancel_user_immediately_removes_public_face(self, tmp_path, monkeypatch):
        """`cancel_user` 当场删分享行 + 报告文件（公开面不等 90 天）。"""
        import src.storage.dao as dao_mod
        from src.storage.dao import UserDAO, get_conn
        from src.storage.share_dao import ShareDAO, owner_tag_for
        db = str(tmp_path / "cancel2.db")
        rep_dir = tmp_path / "reports2"
        rep_dir.mkdir()
        monkeypatch.setenv("FORTUNE_DB_PATH", db)
        monkeypatch.setattr(dao_mod, "_DB_PATH", db, raising=False)
        monkeypatch.setenv("CHARTS_DIR", str(tmp_path / "charts2"))
        me = "k77-cancel-face"
        dao = UserDAO(db)
        sdao = ShareDAO(get_conn())
        sdao.insert("MINE0002", {"pairs": []}, owner_tag=owner_tag_for(me))
        f = _write_report(rep_dir, "dddd4444", owner_uid=me)
        # cancel_user 内部调 purge_report_files（默认指向生产目录）——本用例把
        # 目录参数固定到 tmp（**绝不碰仓内/生产 data/reports**）
        orig_purge = dao_mod.purge_report_files
        monkeypatch.setattr(
            dao_mod, "purge_report_files",
            lambda uid, **kw: orig_purge(uid, reports_dir=str(rep_dir),
                                         charts_dir=str(tmp_path / "charts2")))
        dao.cancel_user(me)
        assert not f.exists(), "注销没有立即删除本人报告文件（公开面仍在）"
        assert sdao.get_state("MINE0002")[0] == "missing"
        assert dao.get_user_status(me) == "cancelled"

    def test_share_page_404_after_report_file_deleted(self, tmp_path, monkeypatch):
        """报告文件被删后，公开分享页立即 404（不再对外提供）。"""
        client, _ = _report_client(tmp_path, monkeypatch)
        p = _write_report(tmp_path, "eeee5555", age_days=1)
        assert client.get("/share/eeee5555").status_code == 200
        p.unlink()
        assert client.get("/share/eeee5555").status_code == 404


# ── T3 删除对话接通服务端（k77-I4）────────────────────────────────────

class TestChatSessionDelete:
    def _env(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        import src.main as main_mod
        from src.security.auth import AuthHandler, set_auth_handler
        from src.storage.session_dao import SessionDAO
        db = str(tmp_path / "sessions.db")
        monkeypatch.setenv("FORTUNE_DB_PATH", db)
        sdao = SessionDAO(db)
        monkeypatch.setattr(main_mod, "session_dao", sdao)
        ah = AuthHandler()
        set_auth_handler(ah)
        client = TestClient(main_mod.app)
        return client, ah, sdao, main_mod

    def _seed(self, sdao, uid, sid, n=2):
        for i in range(n):
            sdao.add_message(uid, "user", f"问题{i}", session_id=sid)

    def test_session_scope_deletes_only_that_conversation(self, tmp_path, monkeypatch):
        client, ah, sdao, _ = self._env(tmp_path, monkeypatch)
        uid = "k77-del"
        self._seed(sdao, uid, "s_aaaa1111")
        self._seed(sdao, uid, "s_bbbb2222")
        h = {"Authorization": f"Bearer {ah.create_user_token(uid)}"}
        r = client.post("/api/chat/sessions/delete",
                        json={"scope": "session", "session_id": "s_aaaa1111"}, headers=h)
        assert r.status_code == 200 and r.json()["status"] == "ok"
        assert r.json()["deleted"] == 2
        assert sdao.count_legacy_sessions(uid) == 0
        # 服务端**真的**删了（不是只回个 ok）：另一段仍在，被删的那段查不到了
        conn = sdao._connect()
        left = conn.execute("SELECT session_id, COUNT(*) FROM sessions WHERE user_id=?"
                            " GROUP BY session_id", (uid,)).fetchall()
        conn.close()
        assert left == [("s_bbbb2222", 2)], left

    def test_legacy_scope_deletes_rows_without_session_id(self, tmp_path, monkeypatch):
        client, ah, sdao, _ = self._env(tmp_path, monkeypatch)
        uid = "k77-legacy"
        for i in range(3):
            sdao.add_message(uid, "user", f"旧问题{i}")          # 无 session_id
        self._seed(sdao, uid, "s_cccc3333")
        h = {"Authorization": f"Bearer {ah.create_user_token(uid)}"}
        r = client.post("/api/chat/sessions/delete", json={"scope": "legacy"}, headers=h)
        assert r.status_code == 200
        assert r.json()["deleted"] == 3
        assert r.json()["legacy_remaining"] == 0                 # 如实回执
        conn = sdao._connect()
        left = conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (uid,)).fetchone()[0]
        conn.close()
        assert left == 2, "legacy 作用域误删了带会话编号的行"

    def test_scope_and_session_id_are_required(self, tmp_path, monkeypatch):
        client, ah, _sdao, _ = self._env(tmp_path, monkeypatch)
        h = {"Authorization": f"Bearer {ah.create_user_token('k77-bad')}"}
        assert client.post("/api/chat/sessions/delete", json={}, headers=h).status_code == 400
        assert client.post("/api/chat/sessions/delete",
                           json={"scope": "all"}, headers=h).status_code == 400
        assert client.post("/api/chat/sessions/delete",
                           json={"scope": "session"}, headers=h).status_code == 400
        # 形态非法的 session_id 同样 400（不执行任何删除）
        assert client.post("/api/chat/sessions/delete",
                           json={"scope": "session", "session_id": ".."},
                           headers=h).status_code == 400

    def test_requires_login_and_is_owner_scoped(self, tmp_path, monkeypatch):
        client, ah, sdao, _ = self._env(tmp_path, monkeypatch)
        me, other = "k77-me", "k77-other"
        self._seed(sdao, other, "s_dddd4444", n=3)
        assert client.post("/api/chat/sessions/delete",
                           json={"scope": "legacy"}).status_code == 401
        h = {"Authorization": f"Bearer {ah.create_user_token(me)}"}
        r = client.post("/api/chat/sessions/delete",
                        json={"scope": "session", "session_id": "s_dddd4444"}, headers=h)
        assert r.status_code == 200 and r.json()["deleted"] == 0   # 别人的删不掉
        conn = sdao._connect()
        left = conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (other,)).fetchone()[0]
        conn.close()
        assert left == 3, "越权删除了他人的对话行"

    def test_delete_sessions_refuses_empty_scope(self):
        """DAO 层兜底：两个作用域都没给 → 返回 0，**绝不**退化成全表删。"""
        import tempfile
        from src.storage.session_dao import SessionDAO
        db = os.path.join(tempfile.mkdtemp(), "s.db")
        sdao = SessionDAO(db)
        sdao.add_message("u1", "user", "x", session_id="s_xxxx1111")
        sdao.add_message("u1", "user", "y")
        assert sdao.delete_sessions("u1") == 0
        conn = sdao._connect()
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2
        conn.close()
        # 空 user_id 一律拒绝
        assert sdao.delete_sessions("", legacy_only=True) == 0


# ── T4 已注销账号不再被推送触达（k77）──────────────────────────────────

class TestNoPushToCancelledAccounts:
    def _dao(self, tmp_path, monkeypatch):
        import src.storage.dao as dao_mod
        from src.storage.dao import UserDAO
        db = str(tmp_path / "push.db")
        monkeypatch.setenv("FORTUNE_DB_PATH", db)
        monkeypatch.setattr(dao_mod, "_DB_PATH", db, raising=False)
        return UserDAO(db)

    def test_pushable_query_excludes_cancelled(self, tmp_path, monkeypatch):
        dao = self._dao(tmp_path, monkeypatch)
        dao.save_user_bazi("u-active", {"bazi": ["甲子", "乙丑", "丙寅", "丁卯"]})
        dao.save_user_bazi("u-cancel", {"bazi": ["甲子", "乙丑", "丙寅", "丁卯"]})
        dao.cancel_user("u-cancel")
        allu = {u["user_id"] for u in dao.get_all_users_with_bazi()}
        push = {u["user_id"] for u in dao.get_pushable_users_with_bazi()}
        assert allu == {"u-active", "u-cancel"}      # 语义未变：本方法只回答"谁填了八字"
        assert push == {"u-active"}, "已注销账号仍在推送名单里"

    def test_daily_and_weekly_batch_skip_cancelled(self, tmp_path, monkeypatch):
        """真跑批次（dry-run）：已注销账号不出现在推送明细里。

        改前实测：`get_all_users_with_bazi()` 不带 status 条件 →
        已注销用户在 90 天保留期内照常收到每日推送（与注销页"不再打扰"承诺相反）。
        """
        from scripts.daily_push import (get_today_ganzhi, run_push_batch,
                                        run_weekly_push_batch)
        dao = self._dao(tmp_path, monkeypatch)
        bazi = {"bazi": ["甲子", "乙丑", "丙寅", "丁卯"], "gender": "男"}
        dao.save_user_bazi("u-live", bazi)
        dao.save_user_bazi("u-gone", bazi)
        dao.cancel_user("u-gone")
        today = get_today_ganzhi()
        for batch in (run_push_batch, run_weekly_push_batch):
            stats = batch(dao, today, dry_run=True)
            ids = {d["user_id"] for d in stats["details"]}
            assert "u-gone" not in ids, f"{batch.__name__} 仍在触达已注销账号：{sorted(ids)}"
            assert "u-live" in ids, f"{batch.__name__} 误伤了正常账号：{sorted(ids)}"

    def test_shared_filter_point_is_fail_closed(self, monkeypatch):
        """三条触达链共用的过滤点：判定失败 → **跳过**（宁可少发一条，不对已注销用户违约）。"""
        import src.main as main_mod

        class _Boom:
            def get_user_status(self, uid):
                raise RuntimeError("db down")

        monkeypatch.setattr(main_mod, "dao", _Boom())
        assert main_mod._drop_cancelled_users(["a", "b"]) == []

        class _Ok:
            def get_user_status(self, uid):
                return "cancelled" if uid == "gone" else "active"

        monkeypatch.setattr(main_mod, "dao", _Ok())
        assert main_mod._drop_cancelled_users(["gone", "live"]) == ["live"]

    def test_jian_batch_filters_cancelled(self, tmp_path, monkeypatch):
        """晨笺/晚安链：`_send_jian_batch` 取到的 uid 名单已过滤注销账号。"""
        import asyncio
        import src.main as main_mod
        from src.storage.jian_dao import JianPrefDAO
        from src.storage.models import connect as db_connect

        dao = self._dao(tmp_path, monkeypatch)
        dao.save_user_bazi("u-jian-gone", {"bazi": ["甲子", "乙丑", "丙寅", "丁卯"]})
        dao.cancel_user("u-jian-gone")
        monkeypatch.setattr(main_mod, "dao", dao)
        jdao = JianPrefDAO(db_connect(str(tmp_path / "push.db")))
        jdao.upsert_pref("u-jian-gone", {"jian_enabled": 1, "jian_time": "07:30",
                                         "bound_status": "bound"})
        monkeypatch.setattr("src.services.wechat_mp.mp_ready", lambda: False)
        stats = asyncio.run(main_mod._send_jian_batch(jdao, "07:30", "jian"))
        assert stats["total"] == 0, "晨笺链仍把已注销账号算进了发送名单"
