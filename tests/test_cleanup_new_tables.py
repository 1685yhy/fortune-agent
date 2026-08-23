"""注销清理覆盖新表测试（Task 11：chart_records/favorites/jian_cards 随注销物理清除）。

- JianPrefDAO 构造收 conn（非路径，见 test_jian_cards.py 同款写法）；
- cancel_user(user_id) 只软删不传时间戳 → 用直更 SQL 把 cancelled_at 回拨到 90 天前，
  使 cleanup_cancelled_accounts(retention_days=90) 判定可清理。
"""
import sys, os, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from src.storage.dao import UserDAO
from src.storage.chart_dao import ChartDAO
from src.storage.favorite_dao import FavoriteDAO
from src.storage.jian_dao import JianPrefDAO


def test_cleanup_covers_new_tables(tmp_path):
    db = str(tmp_path / "t.db")
    dao = UserDAO(db)
    ChartDAO(db).save_chart("u9", 1, {"year": 1990, "month": 5, "day": 20, "hour": 15,
                                      "minute": 0, "city": "北京", "gender": "男"},
                            {"bazi": ["a", "b", "c", "d"]})
    FavoriteDAO(db).add("u9", "chat", "m1", "s")
    # JianPrefDAO 收连接（check_same_thread=False，与生产 get_conn 同款）
    jian_conn = sqlite3.connect(db, check_same_thread=False)
    JianPrefDAO(jian_conn).save_card("u9", "2026-08-23", {"day_ganzhi": "甲子"})

    dao.cancel_user("u9")
    # cancel_user 只软删不接收时间戳：直更回拨到 90 天前 → 可清理
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET cancelled_at=? WHERE user_id=?",
                 ("2026-05-01 00:00:00", "u9"))
    conn.commit()
    conn.close()

    stats = dao.cleanup_cancelled_accounts(
        retention_days=90, memory_dir=str(tmp_path / "mem"))

    # 断言新表数据随注销清除
    assert ChartDAO(db).get_latest_chart("u9") is None
    assert len(FavoriteDAO(db).list_favorites("u9")) == 0
    assert JianPrefDAO(jian_conn).get_card("u9") is None
    assert stats.get("removed_users", 0) >= 1
