"""灯语库存储(方案·灯下漫谈)。表 night_lamp: 每用户每夜一条灯语(文字+语音),可收藏。"""
import time

class LampDAO:
    def __init__(self, conn):
        self.conn = conn
        self.conn.execute("""CREATE TABLE IF NOT EXISTS night_lamp (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            date TEXT NOT NULL,
            text TEXT NOT NULL,
            audio_url TEXT DEFAULT '',
            favorite INTEGER DEFAULT 0,
            created_at REAL,
            UNIQUE(user_id, date)
        )""")
        self.conn.commit()

    def get_lamp(self, user_id: str, date: str):
        row = self.conn.execute(
            "SELECT * FROM night_lamp WHERE user_id=? AND date=?",
            (user_id, date)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM night_lamp").description]
        return dict(zip(cols, row))

    def upsert_lamp(self, user_id: str, date: str, text: str, audio_url: str = ""):
        self.conn.execute(
            """INSERT INTO night_lamp (user_id, date, text, audio_url, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(user_id, date) DO UPDATE SET
               text=excluded.text, audio_url=excluded.audio_url""",
            (user_id, date, text, audio_url, time.time()))
        self.conn.commit()

    def set_favorite(self, user_id: str, date: str, fav: int):
        self.conn.execute(
            "UPDATE night_lamp SET favorite=? WHERE user_id=? AND date=?",
            (fav, user_id, date))
        self.conn.commit()

    def list_history(self, user_id: str, limit: int = 100) -> list:
        rows = self.conn.execute(
            "SELECT * FROM night_lamp WHERE user_id=? ORDER BY date DESC LIMIT ?",
            (user_id, limit)).fetchall()
        cols = [d[0] for d in self.conn.execute("SELECT * FROM night_lamp").description]
        return [dict(zip(cols, r)) for r in rows]
