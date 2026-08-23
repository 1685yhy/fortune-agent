"""晨笺内容落库 jian_cards 测试（Task 8：缺口①——晨笺生成时落库，对话可直读）。"""
import sys, os, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from src.storage.jian_dao import JianPrefDAO


@pytest.fixture
def dao(tmp_path):
    # 与生产一致: check_same_thread=False(dao.get_conn 同款)
    return JianPrefDAO(sqlite3.connect(str(tmp_path / "j.db"), check_same_thread=False))


def test_card_save_and_get(tmp_path, dao):
    card = {"date": "2026-08-23", "day_ganzhi": "甲子",
            "suitable": ["出行", "洽谈"], "unsuitable": ["借贷"],
            "quote": "金句", "book": "古籍", "private_line": "今日宜静心"}
    dao.save_card("u1", "2026-08-23", card)
    r = dao.get_card("u1")
    assert r and r["card_json"]["day_ganzhi"] == "甲子"
    assert "宜静心" in r["card_json"]["private_line"]
    # 密文
    row = sqlite3.connect(str(tmp_path / "j.db")).execute(
        "SELECT card_enc FROM jian_cards").fetchone()
    assert ":" in row[0] and "甲子" not in row[0]


def test_card_get_by_date_and_latest(dao):
    dao.save_card("u1", "2026-08-22", {"date": "2026-08-22", "day_ganzhi": "癸亥",
                                       "suitable": [], "unsuitable": [], "quote": "",
                                       "book": "", "private_line": "a"})
    dao.save_card("u1", "2026-08-23", {"date": "2026-08-23", "day_ganzhi": "甲子",
                                       "suitable": [], "unsuitable": [], "quote": "",
                                       "book": "", "private_line": "b"})
    assert dao.get_card("u1", "2026-08-22")["card_json"]["day_ganzhi"] == "癸亥"
    assert dao.get_card("u1")["card_json"]["day_ganzhi"] == "甲子"  # 最近一张
    assert dao.get_card("u1", "2099-01-01") is None
    assert dao.get_card("nobody") is None


def test_card_upsert_same_day(dao):
    """同 user_id+date 重复落库: ON CONFLICT 更新不产生重复行。"""
    card = {"date": "2026-08-23", "day_ganzhi": "甲子", "suitable": [], "unsuitable": [],
            "quote": "", "book": "", "private_line": "第一版"}
    dao.save_card("u1", "2026-08-23", card)
    card["private_line"] = "第二版"
    dao.save_card("u1", "2026-08-23", card)
    r = dao.get_card("u1")
    assert r["card_json"]["private_line"] == "第二版"
    cnt = dao.conn.execute("SELECT COUNT(*) FROM jian_cards WHERE user_id='u1'").fetchone()[0]
    assert cnt == 1
