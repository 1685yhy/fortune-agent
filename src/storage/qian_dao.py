"""抽灵签存储。表 qian_saves(签卡收藏)。

- qian_saves: 收藏的签卡。UNIQUE(user_id, no) 防重复收藏 —— 同一支签
  只能收藏一次,重复收藏由 API 层返回 already 提示(不报错)。
- drawn_at: 收藏时间(real epoch)。轻量表,无外键,归属由 user_id 隔离。
"""
import time
from typing import Optional


class QianDAO:
    def __init__(self, conn):
        self.conn = conn
        self.conn.execute("""CREATE TABLE IF NOT EXISTS qian_saves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            no INTEGER NOT NULL,
            drawn_at REAL,
            UNIQUE (user_id, no)
        )""")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_qian_saves_user ON qian_saves (user_id, id)")
        self.conn.commit()

    def save(self, user_id: str, no: int) -> tuple:
        """收藏一支签(幂等:UNIQUE 防重)。返回 (saved, already):
        - (True, False) 首次收藏成功
        - (False, True) 已收藏(UNIQUE 冲突,不覆盖原收藏时间)
        """
        try:
            self.conn.execute(
                "INSERT INTO qian_saves (user_id, no, drawn_at) VALUES (?,?,?)",
                (user_id, no, time.time()))
            self.conn.commit()
            return True, False
        except Exception:
            return False, True

    def is_saved(self, user_id: str, no: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM qian_saves WHERE user_id=? AND no=?",
            (user_id, no)).fetchone()
        return row is not None

    def list_history(self, user_id: str, limit: int = 20) -> list:
        """收藏历史(最新在前,上限 limit 条)。"""
        rows = self.conn.execute(
            "SELECT no, drawn_at FROM qian_saves WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)).fetchall()
        return [{"no": r[0], "drawn_at": r[1]} for r in rows]
