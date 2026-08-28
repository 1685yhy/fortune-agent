"""抽灵签存储。表 qian_saves(签卡收藏)。

- qian_saves: 收藏的签卡。UNIQUE(user_id, no, kind) 防重复收藏 —— 同一签种下
  同一支签只能收藏一次,重复收藏由 API 层返回 already 提示(不报错)。
  kind ∈ original/guanyin/guandi/xuanwushan,缺省 'original'。
- drawn_at: 收藏时间(real epoch)。轻量表,无外键,归属由 user_id 隔离。

- 迁移(B5-1 三签种): 旧库表结构 UNIQUE(user_id, no) 无 kind 列。构造时
  PRAGMA table_info 检查,缺 kind 列则整表重建(rename→建新表→拷旧数据
  kind='original'→drop 旧表),旧收藏零丢失;UNIQUE 含 kind 后同 no 跨签种
  可分别收藏。
"""
import time
from typing import Optional

_KINDS = ("original", "guanyin", "guandi", "xuanwushan")


class QianDAO:
    def __init__(self, conn):
        self.conn = conn
        self.conn.execute("""CREATE TABLE IF NOT EXISTS qian_saves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            no INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'original',
            drawn_at REAL,
            UNIQUE (user_id, no, kind)
        )""")
        self._migrate_kind()
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_qian_saves_user ON qian_saves (user_id, id)")
        self.conn.commit()

    def _migrate_kind(self):
        """旧库(无 kind 列)懒迁移:整表重建,UNIQUE 升级为 (user_id, no, kind)。
        幂等:新库/已迁移库不执行。"""
        cols = {r[1] for r in self.conn.execute(
            "PRAGMA table_info(qian_saves)").fetchall()}
        if "kind" in cols:
            return
        self.conn.execute("ALTER TABLE qian_saves RENAME TO qian_saves_old")
        self.conn.execute("""CREATE TABLE qian_saves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            no INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'original',
            drawn_at REAL,
            UNIQUE (user_id, no, kind)
        )""")
        self.conn.execute(
            "INSERT INTO qian_saves (id, user_id, no, kind, drawn_at) "
            "SELECT id, user_id, no, 'original', drawn_at FROM qian_saves_old")
        self.conn.execute("DROP TABLE qian_saves_old")

    def save(self, user_id: str, no: int, kind: str = "original") -> tuple:
        """收藏一支签(幂等:UNIQUE 防重)。返回 (saved, already):
        - (True, False) 首次收藏成功
        - (False, True) 已收藏(UNIQUE 冲突,不覆盖原收藏时间)
        """
        if kind not in _KINDS:
            raise ValueError(f"未知签种: {kind}")
        try:
            self.conn.execute(
                "INSERT INTO qian_saves (user_id, no, kind, drawn_at) VALUES (?,?,?,?)",
                (user_id, no, kind, time.time()))
            self.conn.commit()
            return True, False
        except Exception:
            return False, True

    def is_saved(self, user_id: str, no: int, kind: str = "original") -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM qian_saves WHERE user_id=? AND no=? AND kind=?",
            (user_id, no, kind)).fetchone()
        return row is not None

    def list_history(self, user_id: str, limit: int = 20,
                     kind: Optional[str] = None) -> list:
        """收藏历史(最新在前,上限 limit 条)。kind 为空 = 不限签种(兼容旧调用)。"""
        if kind is None:
            rows = self.conn.execute(
                "SELECT no, kind, drawn_at FROM qian_saves WHERE user_id=? "
                "ORDER BY id DESC LIMIT ?",
                (user_id, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT no, kind, drawn_at FROM qian_saves WHERE user_id=? AND kind=? "
                "ORDER BY id DESC LIMIT ?",
                (user_id, kind, limit)).fetchall()
        return [{"no": r[0], "kind": r[1], "drawn_at": r[2]} for r in rows]
