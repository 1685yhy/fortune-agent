"""收藏后端化测试（Task 9：缺口②——favorites 表 + 4 API + RecordQuery 全链路直读）。

- DAO 层：UNIQUE(user_id,type,ref_id) 幂等增删查列（简报 Step 1 测试原文）。
- API 层：require_user 鉴权 / type 白名单 / summary 截 100 字 / 用户隔离 / import 幂等。
- 全链路：真实 FavoriteDAO + 真实 JianPrefDAO 落库 → RecordQuery.direct_query
  对话直读「我收藏过」/「今早的晨笺」（装配层统一注入，T8 审查 ⚠️ 项）。
"""
import os
import sys
import sqlite3

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.storage.favorite_dao import FavoriteDAO

# ══════════════════════════ DAO 层（简报 Step 1 原文测试） ══════════════════════════

def test_favorite_crud(tmp_path):
    dao = FavoriteDAO(str(tmp_path / "f.db"))
    dao.add("u1", "chat", "msg-1", "收藏的回复摘要")
    dao.add("u1", "chat", "msg-1", "重复添加幂等")
    assert len(dao.list_favorites("u1")) == 1
    assert dao.has("u1", "chat", "msg-1")
    dao.remove("u1", "chat", "msg-1")
    assert not dao.has("u1", "chat", "msg-1")
    dao.add("u2", "chat", "msg-9", "别家")
    assert len(dao.list_favorites("u1")) == 0


def test_favorite_limit_and_imported_flag(tmp_path):
    dao = FavoriteDAO(str(tmp_path / "f2.db"))
    for i in range(60):
        dao.add("u1", "chat", f"msg-{i}", f"摘要{i}")
    # 默认 limit=50；可显式覆盖
    assert len(dao.list_favorites("u1")) == 50
    assert len(dao.list_favorites("u1", limit=10)) == 10
    # 导入批次标记列（imported=1；本地清标记是前端行为，后端仅幂等）
    dao.add("u1", "jian", "2026-08-23", "晨笺卡", imported=1)
    rows = dao.list_favorites("u1")
    jian_row = [r for r in rows if r["type"] == "jian"][0]
    assert jian_row["imported"] == 1
    assert jian_row["summary"] == "晨笺卡"


def test_favorite_type_field_roundtrip(tmp_path):
    """type 白名单外亦允许落库（白名单校验在 API 层），各类别可共存。"""
    dao = FavoriteDAO(str(tmp_path / "f3.db"))
    for t in ("chat", "jian", "qian", "ming", "lamp"):
        dao.add("u1", t, f"ref-{t}", f"摘要-{t}")
    assert len(dao.list_favorites("u1")) == 5


# ══════════════════════════ API 层（TestClient + 假 token，仿 scripts/test_qian_api.py） ══════════════════════════

@pytest.fixture
def api_env(tmp_path):
    """注入独立临时 DB + 假 token 的 TestClient 环境。"""
    import src.storage.dao as dao_mod
    import src.api.favorites as fav_mod
    from src.main import app
    from src.security.auth import AuthHandler, set_auth_handler
    from starlette.testclient import TestClient

    prev_db_path = dao_mod._DB_PATH
    dao_mod._DB_PATH = str(tmp_path / "api.db")

    test_dao = FavoriteDAO(str(tmp_path / "api.db"))
    prev_dao = fav_mod._dao
    fav_mod._dao = test_dao
    app.state.fav_dao = test_dao

    _auth = AuthHandler()
    set_auth_handler(_auth)
    UID = "fav-test-user"
    UID2 = "fav-test-user-b"
    client = TestClient(app)
    yield {
        "client": client,
        "dao": test_dao,
        "h": {"Authorization": f"Bearer {_auth.create_user_token(UID)}"},
        "h2": {"Authorization": f"Bearer {_auth.create_user_token(UID2)}"},
    }
    fav_mod._dao = prev_dao
    dao_mod._DB_PATH = prev_db_path


def test_api_requires_auth(api_env):
    client = api_env["client"]
    assert client.get("/api/favorites").status_code == 401
    assert client.post("/api/favorites", json={"type": "chat", "ref_id": "m1", "summary": "s"}).status_code == 401
    assert client.delete("/api/favorites?type=chat&ref_id=m1").status_code == 401
    assert client.post("/api/favorites/import", json={"items": []}).status_code == 401


def test_api_add_list_delete(api_env):
    client, h, h2 = api_env["client"], api_env["h"], api_env["h2"]
    # 新增
    r = client.post("/api/favorites", json={"type": "chat", "ref_id": "msg-1", "summary": "收藏的回复摘要"}, headers=h)
    assert r.status_code == 200
    assert r.json()["success"] and r.json()["already"] is False
    # 重复新增幂等（UNIQUE）
    r = client.post("/api/favorites", json={"type": "chat", "ref_id": "msg-1", "summary": "重复"}, headers=h)
    assert r.json()["already"] is True
    # 列表
    r = client.get("/api/favorites", headers=h)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["type"] == "chat" and items[0]["ref_id"] == "msg-1"
    assert items[0]["summary"] == "收藏的回复摘要"
    # 用户隔离：他人列表为空
    assert client.get("/api/favorites", headers=h2).json()["items"] == []
    # 删除
    r = client.delete("/api/favorites?type=chat&ref_id=msg-1", headers=h)
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert client.get("/api/favorites", headers=h).json()["items"] == []
    # 重复删除幂等
    r = client.delete("/api/favorites?type=chat&ref_id=msg-1", headers=h)
    assert r.json()["deleted"] is False


def test_api_type_whitelist_and_summary_truncate(api_env):
    client, h = api_env["client"], api_env["h"]
    # type 白名单：chat/jian/qian/ming/lamp 之外 → 400
    for bad in ("", "evil", "user", "fav"):
        r = client.post("/api/favorites", json={"type": bad, "ref_id": "x", "summary": "s"}, headers=h)
        assert r.status_code == 400, f"type={bad!r} 应被拒绝"
    # 缺 ref_id → 400
    assert client.post("/api/favorites", json={"type": "chat", "summary": "s"}, headers=h).status_code == 400
    # summary 截 100 字
    long_summary = "长" * 200
    client.post("/api/favorites", json={"type": "chat", "ref_id": "long", "summary": long_summary}, headers=h)
    items = client.get("/api/favorites", headers=h).json()["items"]
    assert len(items[0]["summary"]) == 100


def test_api_import_idempotent(api_env):
    client, h, h2 = api_env["client"], api_env["h"], api_env["h2"]
    items = [
        {"type": "chat", "ref_id": "m-1", "summary": "本地收藏一"},
        {"type": "chat", "ref_id": "m-2", "summary": "本地收藏二"},
        {"type": "jian", "ref_id": "2026-08-23", "summary": "晨笺卡"},
    ]
    r = client.post("/api/favorites/import", json={"items": items}, headers=h)
    assert r.status_code == 200
    assert r.json()["imported"] == 3
    # 重复导入幂等：UNIQUE 防重，只算新增
    r = client.post("/api/favorites/import", json={"items": items}, headers=h)
    assert r.json()["imported"] == 0
    assert len(client.get("/api/favorites", headers=h).json()["items"]) == 3
    # 他人导入互不影响
    assert len(client.get("/api/favorites", headers=h2).json()["items"]) == 0
    # 白名单外条目 → 400
    r = client.post("/api/favorites/import", json={"items": [{"type": "bad", "ref_id": "x", "summary": "s"}]}, headers=h)
    assert r.status_code == 400
    # 空 items → 0（不报错）
    assert client.post("/api/favorites/import", json={"items": []}, headers=h).json()["imported"] == 0


def test_api_fail_closed_when_dao_missing(api_env):
    """fail-closed：DAO 未装配 → 503，绝不假 200（与 user.py 服务未就绪模式一致）。"""
    import src.api.favorites as fav_mod
    client = api_env["client"]
    prev = fav_mod._dao
    fav_mod._dao = None
    try:
        assert client.get("/api/favorites", headers=api_env["h"]).status_code == 503
        assert client.post("/api/favorites", json={"type": "chat", "ref_id": "x", "summary": "s"},
                           headers=api_env["h"]).status_code == 503
    finally:
        fav_mod._dao = prev


# ══════════════════════════ RecordQuery 全链路（真实 DAO + 真实落库） ══════════════════════════

def _build_rq(db):
    from src.bot.record_query import RecordQuery
    from src.storage.chart_dao import ChartDAO
    from src.storage.dao import UserDAO
    from src.storage.person_dao import PersonDAO
    from src.storage.session_dao import SessionDAO
    return RecordQuery(UserDAO(db), PersonDAO(db), SessionDAO(db), ChartDAO(db),
                       fav_dao=FavoriteDAO(db))


def test_record_query_fav_full_chain(tmp_path):
    """_q_收藏 全链路端到端：真实 FavoriteDAO 落库 → RecordQuery.direct_query 直读。"""
    db = str(tmp_path / "rq.db")
    rq = _build_rq(db)
    rq.fav_dao.add("u1", "chat", "msg-1", "财运大吉的回复摘要")
    rq.fav_dao.add("u1", "chat", "msg-2", "感情顺利的回复摘要")
    rq.fav_dao.add("u2", "chat", "msg-9", "别人的收藏")
    out = rq.direct_query("u1", "我收藏过什么")
    assert out and "财运大吉" in out and "感情顺利" in out and "别人的收藏" not in out
    # 无收藏用户 → None（不伪造，走正常流程）
    assert rq.direct_query("u3", "我收藏的") is None


def test_record_query_jian_full_chain(tmp_path):
    """_q_晨笺 全链路端到端：真实 JianPrefDAO 落库 jian_cards → RecordQuery.direct_query 直读。"""
    from src.storage.jian_dao import JianPrefDAO
    db = str(tmp_path / "rqj.db")
    rq = _build_rq(db)
    jdao = JianPrefDAO(sqlite3.connect(db, check_same_thread=False))
    jdao.save_card("u1", "2026-08-23",
                   {"date": "2026-08-23", "day_ganzhi": "甲子",
                    "suitable": ["出行", "洽谈"], "unsuitable": ["借贷"],
                    "quote": "", "book": "", "private_line": ""})
    rq.jian_dao = jdao
    out = rq.direct_query("u1", "今早的晨笺是什么")
    assert out and "甲子" in out and "出行" in out


def test_handler_assembly_injects_light_daos(tmp_path):
    """装配层统一注入（T8 审查 ⚠️ 项）：MessageHandler 构造后 record_query 的
    轻量 DAO（qian/ming/lamp/zeri/jian/fav）全部就位，且全链路端到端直读可用。"""
    from unittest.mock import Mock
    from src.bot.handler import MessageHandler

    db = str(tmp_path / "h.db")
    mock_dao = Mock()
    mock_dao.db_path = db  # 真实文件路径 → RecordQuery 装配路径（test_bot 用 :memory:）

    handler = MessageHandler(
        engine=Mock(), ziwei_engine=Mock(), liuyao_engine=Mock(),
        fengshui_engine=Mock(), mianxiang_engine=Mock(), zeri_engine=Mock(),
        retriever=Mock(), llm=Mock(), dao=mock_dao, dream_engine=Mock(),
    )
    rq = handler.record_query
    assert rq is not None
    for name in ("qian_dao", "ming_dao", "lamp_dao", "zeri_dao", "jian_dao", "fav_dao"):
        assert getattr(rq, name) is not None, f"装配缺失: {name}"
    # 端到端：真实 DAO 落库 → 对话直读（收藏 + 晨笺双链路）
    rq.fav_dao.add("u1", "chat", "msg-1", "被收藏的好回复")
    rq.jian_dao.save_card("u1", "2026-08-23",
                          {"date": "2026-08-23", "day_ganzhi": "甲子",
                           "suitable": ["出行"], "unsuitable": [],
                           "quote": "", "book": "", "private_line": ""})
    out = rq.direct_query("u1", "我收藏过什么")
    assert out and "被收藏的好回复" in out
    out = rq.direct_query("u1", "今早的晨笺呢")
    assert out and "甲子" in out


def test_handler_assembly_none_guard_without_db_path():
    """无 db_path（dao.db_path 为空串）→ record_query 保持 None，不炸（T7 None 守卫兜底）。"""
    from unittest.mock import Mock
    from src.bot.handler import MessageHandler
    mock_dao = Mock()
    mock_dao.db_path = ""  # 空串 → 装配路径跳过（Mock 缺省属性会返回 Mock 导致 Path TypeError，须显式置空）
    handler = MessageHandler(
        engine=Mock(), ziwei_engine=Mock(), liuyao_engine=Mock(),
        fengshui_engine=Mock(), mianxiang_engine=Mock(), zeri_engine=Mock(),
        retriever=Mock(), llm=Mock(), dao=mock_dao, dream_engine=Mock(),
    )
    assert handler.record_query is None
