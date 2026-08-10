"""晨笺订阅偏好存储。表 jian_prefs: user_id 主键; 开关/时间/绑定状态。"""
import json, time

class JianPrefDAO:
    def __init__(self, conn):
        self.conn = conn
        self.conn.execute("""CREATE TABLE IF NOT EXISTS jian_prefs (
            user_id TEXT PRIMARY KEY,
            jian_enabled INTEGER DEFAULT 0,
            jian_time TEXT DEFAULT '07:30',
            night_enabled INTEGER DEFAULT 0,
            night_time TEXT DEFAULT '23:00',
            bound_status TEXT DEFAULT 'unbound',
            mp_openid TEXT DEFAULT '',
            updated_at REAL
        )""")
        self.conn.commit()

    def get_pref(self, user_id: str):
        row = self.conn.execute("SELECT * FROM jian_prefs WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM jian_prefs").description]
        return dict(zip(cols, row))

    def upsert_pref(self, user_id: str, prefs: dict):
        cur = self.conn.execute("SELECT * FROM jian_prefs WHERE user_id=?", (user_id,)).fetchone()
        cols = [d[0] for d in self.conn.execute("SELECT * FROM jian_prefs").description]
        data = dict(zip(cols, cur)) if cur else {}
        data.update(prefs)
        data["user_id"] = user_id
        data["updated_at"] = time.time()
        # 首次插入时补全缺失列,回退到建表 DEFAULT(INSERT VALUES 要求所有命名参数齐全)
        defaults = {"jian_enabled": 0, "jian_time": "07:30", "night_enabled": 0,
                    "night_time": "23:00", "bound_status": "unbound", "mp_openid": ""}
        for k, v in defaults.items():
            data.setdefault(k, v)
        self.conn.execute("""INSERT INTO jian_prefs (user_id, jian_enabled, jian_time,
            night_enabled, night_time, bound_status, mp_openid, updated_at)
            VALUES (:user_id,:jian_enabled,:jian_time,:night_enabled,:night_time,:bound_status,:mp_openid,:updated_at)
            ON CONFLICT(user_id) DO UPDATE SET
            jian_enabled=excluded.jian_enabled, jian_time=excluded.jian_time,
            night_enabled=excluded.night_enabled, night_time=excluded.night_time,
            bound_status=excluded.bound_status, mp_openid=excluded.mp_openid,
            updated_at=excluded.updated_at""", data)
        self.conn.commit()

    def list_enabled_at(self, time_hm: str, kind: str) -> list:
        col, time_col = ("jian_enabled", "jian_time") if kind == "jian" else ("night_enabled", "night_time")
        rows = self.conn.execute(
            f"SELECT user_id FROM jian_prefs WHERE {col}=1 AND {time_col}=? AND bound_status='bound'",
            (time_hm,)).fetchall()
        return [r[0] for r in rows]

    def count_enabled(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM jian_prefs WHERE jian_enabled=1 OR night_enabled=1").fetchone()
        return row[0]
