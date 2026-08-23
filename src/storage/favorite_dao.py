"""收藏存储（Task 9：缺口②——收藏后端化）。表 favorites。

- favorites: 对话收藏统一落库。UNIQUE(user_id, type, ref_id) 防重复收藏
  （重复 add 幂等不报错、不覆盖）；type ∈ chat/jian/qian/ming/lamp；
  imported 标记本地导入批次（本地清标记是前端行为，后端仅依赖 UNIQUE 幂等）。
- 构造入参为 db_path（简报接口契约；与 qian/ming/lamp/zeri 等传 conn 的
  轻量表不同——供 handler 装配层与 API 层从各自 db_path 直接实例化）。
- 连接进程级共享（check_same_thread=False），写操作锁内串行（与
  jian_dao/zeri_dao 同款修复，防并发 upsert/读写互相覆盖）。
"""
import sqlite3
import threading
import time


class FavoriteDAO:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self.conn.execute("""CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            type TEXT NOT NULL,        -- chat/jian/qian/ming/lamp
            ref_id TEXT NOT NULL,
            summary TEXT DEFAULT '',
            imported INTEGER DEFAULT 0,  -- 1=本地导入批次（防重复导入标记，本地清标记为前端行为）
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, type, ref_id)
        )""")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites (user_id, id)")
        self.conn.commit()

    def add(self, user_id: str, type_: str, ref_id: str, summary: str = "",
            imported: int = 0) -> int:
        """收藏一条（幂等）。UNIQUE(user_id,type,ref_id) 防重：

        - 首次 → 插入并返回新行 id；
        - 重复 → 不更新不报错，返回 0（已存在）。
        """
        with self._lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO favorites (user_id, type, ref_id, summary, imported) "
                "VALUES (?,?,?,?,?)",
                (user_id, type_, ref_id, summary or "", 1 if imported else 0))
            self.conn.commit()
            # 幂等判定：rowcount=1 为真插入（返回新行 id）；=0 为 UNIQUE 冲突已存在（返回 0）。
            # 不能用 lastrowid——被 IGNORE 的插入会残留上一条成功语句的 rowid，误判为已收藏。
            return cur.lastrowid if cur.rowcount else 0

    def remove(self, user_id: str, type_: str, ref_id: str) -> None:
        """取消收藏（幂等：不存在也不报错）。"""
        with self._lock:
            self.conn.execute(
                "DELETE FROM favorites WHERE user_id=? AND type=? AND ref_id=?",
                (user_id, type_, ref_id))
            self.conn.commit()

    def list_favorites(self, user_id: str, limit: int = 50) -> list:
        """收藏列表（最新在前）。每项含 id/type/ref_id/summary/imported/created_at。"""
        rows = self.conn.execute(
            "SELECT id, user_id, type, ref_id, summary, imported, created_at "
            "FROM favorites WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, max(int(limit), 1))).fetchall()
        return [
            {"id": r[0], "user_id": r[1], "type": r[2], "ref_id": r[3],
             "summary": r[4], "imported": r[5], "created_at": r[6]}
            for r in rows
        ]

    def has(self, user_id: str, type_: str, ref_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM favorites WHERE user_id=? AND type=? AND ref_id=?",
            (user_id, type_, ref_id)).fetchone()
        return row is not None
