"""匿名对话分享存储。表 share_entries: 分享落地页数据。

- id: 8 位 base62 短随机串(主键),由调用方生成、不泄露用户身份;
- content: JSON 字符串 {"pairs": [{u, tag, content}...], "dateText": str};
- created_at: 创建时间(real epoch)。
- 红线:匿名分享 —— 本表不落任何用户标识(user_id / openid 一律不存),
  扫码/网页侧无鉴权,内容即公开数据。
"""
import json
import sqlite3
import time


class ShareDAO:
    def __init__(self, conn):
        self.conn = conn
        self.conn.execute("""CREATE TABLE IF NOT EXISTS share_entries (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            created_at REAL
        )""")
        self.conn.commit()

    def insert(self, share_id: str, content: dict) -> bool:
        """写入一条匿名分享。返回是否成功:
        - 主键冲突(小概率)或异常 → False,由调用方换新 id 重试。
        """
        try:
            self.conn.execute(
                "INSERT INTO share_entries (id, content, created_at) VALUES (?,?,?)",
                (share_id, json.dumps(content, ensure_ascii=False), time.time()))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception:
            return False

    def get(self, share_id: str):
        """按 id 读取内容 dict;不存在或损坏返回 None。"""
        row = self.conn.execute(
            "SELECT content FROM share_entries WHERE id=?",
            (share_id,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return None
