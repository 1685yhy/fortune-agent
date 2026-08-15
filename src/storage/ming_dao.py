"""AI 取名存储。表 ming_saves(名笺收藏) + ming_quota(免费生成日额度)。

- ming_saves: 收藏的名笺。UNIQUE(user_id, full_name) 防重复收藏。
  只存脱敏摘要(名/姓/性别/分数/风格),不含生辰——生辰属于隐私红线,
  名笺数据一律不落库生辰。
- ming_quota: 免费档每日生成次数(方案: 登录后 3 次/日,防爬防刷;
  会员/体验模式不受限,由 API 层判定后跳过)。
"""
import time


class MingDAO:
    def __init__(self, conn):
        self.conn = conn
        self.conn.execute("""CREATE TABLE IF NOT EXISTS ming_saves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            surname TEXT NOT NULL,
            given TEXT NOT NULL,
            gender TEXT NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            style_note TEXT NOT NULL DEFAULT '',
            saved_at REAL,
            UNIQUE (user_id, surname, given)
        )""")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ming_saves_user ON ming_saves (user_id, id)")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS ming_quota (
            user_id TEXT NOT NULL,
            day TEXT NOT NULL,
            cnt INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day)
        )""")
        self.conn.commit()

    # ── 名笺收藏 ──

    def save(self, user_id: str, surname: str, given: str,
             gender: str, score: int, style_note: str = "") -> tuple:
        """收藏一张名笺(幂等: UNIQUE 防重)。返回 (saved, already)。"""
        try:
            self.conn.execute(
                "INSERT INTO ming_saves (user_id, surname, given, gender, score, style_note, saved_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (user_id, surname, given, gender, score, style_note, time.time()))
            self.conn.commit()
            return True, False
        except Exception:
            return False, True

    def list_saved(self, user_id: str, limit: int = 50) -> list:
        """已收藏名笺(最新在前)。"""
        rows = self.conn.execute(
            "SELECT surname, given, gender, score, style_note, saved_at"
            " FROM ming_saves WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)).fetchall()
        return [
            {"full": r[0] + r[1], "surname": r[0], "given": r[1],
             "gender": r[2], "score": r[3], "style_note": r[4], "saved_at": r[5]}
            for r in rows
        ]

    # ── 免费生成日额度 ──

    def consume_quota(self, user_id: str, day: str, limit: int) -> int:
        """尝试消费一次免费生成额度。返回剩余次数(>=0 表示放行,<0 表示超限)。"""
        row = self.conn.execute(
            "SELECT cnt FROM ming_quota WHERE user_id=? AND day=?",
            (user_id, day)).fetchone()
        cnt = row[0] if row else 0
        if cnt >= limit:
            return -1
        if row:
            self.conn.execute(
                "UPDATE ming_quota SET cnt=cnt+1 WHERE user_id=? AND day=?",
                (user_id, day))
        else:
            self.conn.execute(
                "INSERT INTO ming_quota (user_id, day, cnt) VALUES (?,?,1)",
                (user_id, day))
        self.conn.commit()
        return limit - cnt - 1
