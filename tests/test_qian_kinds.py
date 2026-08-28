"""B5-1 三签种测试: 观音灵签 100 / 关帝灵签 100 / 玄武山签 51(批次 5 K)。

覆盖:
- 签库数量与结构完整性(poem 恰 4 行 / jx / cls / jie / suo 全字段)
- 等级归一化: jx ∈ 上上签|上吉签|中吉签|中平签|下签, cls 与 jx 三档一致
- 接口: draw 按 kind 返回对应签种 / 无效 kind 400 / save 跨签种同 no 不冲突
  / history 按 kind 过滤
- qian_saves 迁移: 旧表(无 kind 列)数据保留,kind 列补 'original',
  UNIQUE(user_id,no) 升级为 (user_id,no,kind) 后可跨签种收藏

运行: .venv/bin/python -m pytest tests/test_qian_kinds.py -q
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["EXPERIENCE_MODE"] = ""

import pytest
from starlette.testclient import TestClient

from src.storage.qian_dao import QianDAO
import src.api.qian as qian_mod

VALID_JX = ("上上签", "上吉签", "中吉签", "中平签", "下签")
JX_CLS = {"上上签": "up", "上吉签": "up", "中吉签": "mid",
          "中平签": "mid", "下签": "low"}
ALL_KINDS = {"original": 8, "guanyin": 100, "guandi": 100, "xuanwushan": 51}


# ─────────────────────────── 签库数据 ───────────────────────────

def test_kind_counts():
    assert {k: len(cards) for k, cards in qian_mod.QIAN_KINDS.items()} == ALL_KINDS
    # 签号全集 1..N 无缺(original 为原型非连续号,其余应连续)
    for kind in ("guanyin", "guandi", "xuanwushan"):
        nos = {c["no"] for c in qian_mod.QIAN_KINDS[kind]}
        assert nos == set(range(1, len(qian_mod.QIAN_KINDS[kind]) + 1)), kind


def test_every_card_structure():
    for kind, cards in qian_mod.QIAN_KINDS.items():
        for c in cards:
            assert isinstance(c["no"], int), (kind, c)
            assert c["jx"] in VALID_JX, (kind, c["no"], c["jx"])
            assert c["cls"] in ("up", "mid", "low"), (kind, c["no"])
            assert c["cls"] == JX_CLS[c["jx"]], (kind, c["no"])
            assert isinstance(c["poem"], list) and len(c["poem"]) == 4, (kind, c["no"])
            assert all(isinstance(l, str) and l.strip() for l in c["poem"]), (kind, c["no"])
            assert isinstance(c["jie"], str) and c["jie"].strip(), (kind, c["no"])
            assert isinstance(c["suo"], str) and c["suo"].strip(), (kind, c["no"])


def test_original_eight_untouched():
    """现 8 支手写签逐字零改动(与 QIAN_LIBRARY 同一对象)"""
    assert qian_mod.QIAN_KINDS["original"] is qian_mod.QIAN_LIBRARY
    assert len(qian_mod.QIAN_LIBRARY) == 8
    assert qian_mod.QIAN_BY_NO[7]["poem"][0] == "枯木逢春再发花"


def test_kind_names_complete():
    assert set(qian_mod.KIND_NAMES) == set(qian_mod.QIAN_KINDS)
    for k, v in qian_mod.KIND_NAMES.items():
        assert v and isinstance(v, str)


# ─────────────────────────── 接口(TestClient + 临时库) ───────────────────────────

@pytest.fixture()
def client(tmp_path):
    import src.storage.dao as dao_mod
    dao_mod._DB_PATH = str(tmp_path / "qian.db")
    _dao = QianDAO(dao_mod.get_conn())
    qian_mod._dao = _dao
    from src.main import app
    app.state.qian_dao = _dao
    from src.security.auth import AuthHandler, set_auth_handler
    _auth = AuthHandler()
    set_auth_handler(_auth)
    token = _auth.create_user_token("qian-kinds-test")
    return TestClient(app), {"Authorization": f"Bearer {token}"}


def test_draw_by_kind(client):
    c, h = client
    for kind in ("guanyin", "guandi", "xuanwushan", "original"):
        card = c.post("/api/qian/draw", json={"kind": kind}, headers=h).json()["card"]
        assert card["no"] in qian_mod.QIAN_KIND_NOS[kind]
        assert card == next(x for x in qian_mod.QIAN_KINDS[kind] if x["no"] == card["no"])
    # 缺省 kind=original
    card = c.post("/api/qian/draw", headers=h).json()["card"]
    assert card["no"] in qian_mod.QIAN_KIND_NOS["original"]


def test_draw_invalid_kind_400(client):
    c, h = client
    r = c.post("/api/qian/draw", json={"kind": "wuxing"}, headers=h)
    assert r.status_code == 400
    assert "签种" in r.json()["detail"]


def test_save_cross_kind_same_no(client):
    c, h = client
    # 观音 1 与 关帝 1 与 玄武山 1 同 no 不同签种 → 均可收藏(不冲突)
    saved = []
    for kind in ("guanyin", "guandi", "xuanwushan"):
        r = c.post("/api/qian/save", json={"no": 1, "kind": kind}, headers=h)
        saved.append(r.json())
        assert r.json()["saved"] is True and r.json()["already"] is False, kind
    # 同 kind 重复收藏 → already
    r = c.post("/api/qian/save", json={"no": 1, "kind": "guanyin"}, headers=h)
    assert r.json()["saved"] is False and r.json()["already"] is True


def test_save_invalid_kind_or_no_400(client):
    c, h = client
    assert c.post("/api/qian/save", json={"no": 1, "kind": "bad"}, headers=h).status_code == 400
    # 观音只有 1-100: 101 非法
    assert c.post("/api/qian/save", json={"no": 101, "kind": "guanyin"}, headers=h).status_code == 400
    # 玄武山只有 1-51: 52 非法
    assert c.post("/api/qian/save", json={"no": 52, "kind": "xuanwushan"}, headers=h).status_code == 400


def test_history_filter_by_kind(client):
    c, h = client
    for kind in ("guanyin", "guandi"):
        c.post("/api/qian/save", json={"no": 5, "kind": kind}, headers=h)
    r = c.get("/api/qian/history?kind=guanyin", headers=h)
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["no"] == 5 and items[0]["kind"] == "guanyin"
    assert items[0]["poem_first"] == \
        next(x for x in qian_mod.QIAN_KINDS["guanyin"] if x["no"] == 5)["poem"][0]
    r = c.get("/api/qian/history?kind=guandi", headers=h)
    assert len(r.json()["items"]) == 1 and r.json()["items"][0]["kind"] == "guandi"
    # 缺省返回全部签种
    r = c.get("/api/qian/history", headers=h)
    assert len(r.json()["items"]) == 2
    assert c.get("/api/qian/history?kind=bad", headers=h).status_code == 400


# ─────────────────────────── qian_saves 迁移 ───────────────────────────

def test_migration_old_schema_keeps_data(tmp_path):
    """旧表(无 kind,UNIQUE(user_id,no))→ 新表: 数据保留 + kind='original'
    + 同 no 跨 kind 可收藏。"""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE qian_saves (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        no INTEGER NOT NULL,
        drawn_at REAL,
        UNIQUE (user_id, no)
    )""")
    conn.execute("INSERT INTO qian_saves (user_id, no, drawn_at) VALUES ('u1', 7, 100.0)")
    conn.execute("INSERT INTO qian_saves (user_id, no, drawn_at) VALUES ('u1', 12, 200.0)")
    conn.execute("INSERT INTO qian_saves (user_id, no, drawn_at) VALUES ('u2', 7, 300.0)")
    conn.commit()

    dao = QianDAO(conn)  # 构造触发迁移
    # 1) kind 列已加,旧行 kind='original'
    rows = conn.execute(
        "SELECT user_id, no, kind, drawn_at FROM qian_saves ORDER BY id").fetchall()
    assert rows == [("u1", 7, "original", 100.0),
                    ("u1", 12, "original", 200.0),
                    ("u2", 7, "original", 300.0)]
    # 2) 同 no 跨 kind 可收藏
    assert dao.save("u1", 7, "guanyin") == (True, False)
    assert dao.save("u1", 7, "guandi") == (True, False)
    # 3) 同 kind 重复收藏 → already
    assert dao.save("u1", 7, "original") == (False, True)
    # 4) 旧数据可被查询
    hist = dao.list_history("u1", limit=20, kind="original")
    assert {r["no"] for r in hist} == {7, 12}
    assert dao.is_saved("u2", 7, "original") is True
    assert dao.is_saved("u1", 7, "guanyin") is True
    conn.close()

    # 幂等: 已迁移库再次构造不再报错
    conn2 = sqlite3.connect(str(db))
    QianDAO(conn2)
    cols = {r[1] for r in conn2.execute("PRAGMA table_info(qian_saves)")}
    assert "kind" in cols
    conn2.close()
