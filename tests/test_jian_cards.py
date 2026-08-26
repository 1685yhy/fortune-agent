"""晨笺内容落库 jian_cards 测试（Task 8：缺口①——晨笺生成时落库，对话可直读）。"""
import sys, os, sqlite3, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from src.storage.jian_dao import JianPrefDAO


@pytest.fixture
def dao(tmp_path):
    # 与生产一致: check_same_thread=False(dao.get_conn 同款)
    return JianPrefDAO(sqlite3.connect(str(tmp_path / "j.db"), check_same_thread=False))


def test_get_card_corrupt_json_fail_open(tmp_path, dao):
    """T8 修复：card_enc 解密后非合法 JSON（脏数据/手动改库）→ 不崩，返回空 card_json。

    照 dao 先例（dao.py get_user_bazi json.loads try/except ValueError,TypeError）：
    单卡损坏只影响该卡，不得把整条直读/晨笺链路打成异常。
    """
    from src.storage.dao import _encrypt_text
    dao.conn.execute(
        "INSERT INTO jian_cards (user_id, date, card_enc, created_at) VALUES (?,?,?,?)",
        ("u1", "2026-08-23", _encrypt_text("not-a-json{{{"), time.time()))
    dao.conn.commit()
    r = dao.get_card("u1")
    assert r is not None and r["date"] == "2026-08-23"
    assert r["card_json"] == {}
    # 同表其他正常卡不受影响
    dao.save_card("u1", "2026-08-24", {"date": "2026-08-24", "day_ganzhi": "甲子",
                                       "suitable": [], "unsuitable": [], "quote": "",
                                       "book": "", "private_line": "b"})
    r2 = dao.get_card("u1")
    assert r2["card_json"]["day_ganzhi"] == "甲子"


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
