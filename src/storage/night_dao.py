"""深夜偏好存储(方案·灯下漫谈)。表 night_prefs: 时段档位/点灯动效/深夜挽留/
灯语定时关闭/私语开关;表 night_remember: "要我记得吗"单轮落库(每用户每晚一次)。"""
import time, threading

class NightPrefDAO:
    def __init__(self, conn):
        self.conn = conn
        self._lock = threading.Lock()
        self.conn.execute("""CREATE TABLE IF NOT EXISTS night_prefs (
            user_id TEXT PRIMARY KEY,
            preset TEXT DEFAULT 'standard',
            effect_enabled INTEGER DEFAULT 1,
            keep_enabled INTEGER DEFAULT 1,
            lamp_timer_min INTEGER DEFAULT 15,
            whisper_enabled INTEGER DEFAULT 1,
            updated_at REAL
        )""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS night_remember (
            user_id TEXT NOT NULL,
            date TEXT NOT NULL,
            category TEXT NOT NULL,
            created_at REAL,
            PRIMARY KEY (user_id, date)
        )""")
        self.conn.commit()

    def get_pref(self, user_id: str):
        row = self.conn.execute("SELECT * FROM night_prefs WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM night_prefs").description]
        return dict(zip(cols, row))

    def upsert_pref(self, user_id: str, prefs: dict):
        with self._lock:
            cur = self.conn.execute("SELECT * FROM night_prefs WHERE user_id=?", (user_id,)).fetchone()
            cols = [d[0] for d in self.conn.execute("SELECT * FROM night_prefs").description]
            data = dict(zip(cols, cur)) if cur else {}
            data.update(prefs)
            data["user_id"] = user_id
            data["updated_at"] = time.time()
            defaults = {"preset": "standard", "effect_enabled": 1, "keep_enabled": 1,
                        "lamp_timer_min": 15, "whisper_enabled": 1}
            for k, v in defaults.items():
                data.setdefault(k, v)
            self.conn.execute("""INSERT INTO night_prefs (user_id, preset, effect_enabled,
                keep_enabled, lamp_timer_min, whisper_enabled, updated_at)
                VALUES (:user_id,:preset,:effect_enabled,:keep_enabled,:lamp_timer_min,:whisper_enabled,:updated_at)
                ON CONFLICT(user_id) DO UPDATE SET
                preset=excluded.preset, effect_enabled=excluded.effect_enabled,
                keep_enabled=excluded.keep_enabled, lamp_timer_min=excluded.lamp_timer_min,
                whisper_enabled=excluded.whisper_enabled, updated_at=excluded.updated_at""", data)
            self.conn.commit()

    def get_remember(self, user_id: str, date: str) -> str | None:
        """当晚是否已"要我记得吗"落库;返回已记分类或 None。"""
        row = self.conn.execute(
            "SELECT category FROM night_remember WHERE user_id=? AND date=?",
            (user_id, date)).fetchone()
        return row[0] if row else None

    def set_remember(self, user_id: str, date: str, category: str):
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO night_remember (user_id, date, category, created_at) "
                "VALUES (?,?,?,?)", (user_id, date, category, time.time()))
            self.conn.commit()
