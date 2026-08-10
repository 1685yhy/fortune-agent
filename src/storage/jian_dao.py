"""晨笺订阅偏好存储。表 jian_prefs: user_id 主键; 开关/时间/绑定状态/连续失败计数。"""
import time, threading

class JianPrefDAO:
    def __init__(self, conn):
        self.conn = conn
        # 写锁: 连接为进程级共享(check_same_thread=False),upsert 的读-改-写
        # 序列需加锁,否则并发 PUT 可能互相覆盖(审查 Important 修复)
        self._lock = threading.Lock()
        self.conn.execute("""CREATE TABLE IF NOT EXISTS jian_prefs (
            user_id TEXT PRIMARY KEY,
            jian_enabled INTEGER DEFAULT 0,
            jian_time TEXT DEFAULT '07:30',
            night_enabled INTEGER DEFAULT 0,
            night_time TEXT DEFAULT '23:00',
            bound_status TEXT DEFAULT 'unbound',
            mp_openid TEXT DEFAULT '',
            fail_count INTEGER DEFAULT 0,
            updated_at REAL
        )""")
        # 老库迁移: fail_count 列由 P1 新增(ALTER 兼容既有 DB,默认 0)
        cols = [d[1] for d in self.conn.execute("PRAGMA table_info(jian_prefs)")]
        if "fail_count" not in cols:
            self.conn.execute("ALTER TABLE jian_prefs ADD COLUMN fail_count INTEGER DEFAULT 0")
        self.conn.commit()

    def get_pref(self, user_id: str):
        row = self.conn.execute("SELECT * FROM jian_prefs WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM jian_prefs").description]
        return dict(zip(cols, row))

    def upsert_pref(self, user_id: str, prefs: dict):
        # 单条语句 sqlite3 在 C 层已串行化,但"读当前行→合并→写回"整段
        # 需在锁内完成,否则并发 upsert 各写自己的快照会丢字段
        with self._lock:
            cur = self.conn.execute("SELECT * FROM jian_prefs WHERE user_id=?", (user_id,)).fetchone()
            cols = [d[0] for d in self.conn.execute("SELECT * FROM jian_prefs").description]
            data = dict(zip(cols, cur)) if cur else {}
            data.update(prefs)
            data["user_id"] = user_id
            data["updated_at"] = time.time()
            # 首次插入时补全缺失列,回退到建表 DEFAULT(INSERT VALUES 要求所有命名参数齐全)
            defaults = {"jian_enabled": 0, "jian_time": "07:30", "night_enabled": 0,
                        "night_time": "23:00", "bound_status": "unbound", "mp_openid": "",
                        "fail_count": 0}
            for k, v in defaults.items():
                data.setdefault(k, v)
            self.conn.execute("""INSERT INTO jian_prefs (user_id, jian_enabled, jian_time,
                night_enabled, night_time, bound_status, mp_openid, fail_count, updated_at)
                VALUES (:user_id,:jian_enabled,:jian_time,:night_enabled,:night_time,:bound_status,:mp_openid,:fail_count,:updated_at)
                ON CONFLICT(user_id) DO UPDATE SET
                jian_enabled=excluded.jian_enabled, jian_time=excluded.jian_time,
                night_enabled=excluded.night_enabled, night_time=excluded.night_time,
                bound_status=excluded.bound_status, mp_openid=excluded.mp_openid,
                fail_count=excluded.fail_count, updated_at=excluded.updated_at""", data)
            self.conn.commit()

    def bump_fail(self, user_id: str):
        """发送失败 +1(连续失败计数;P1: 累计≥3 次由调度方标记订阅失效)。"""
        with self._lock:
            self.conn.execute("UPDATE jian_prefs SET fail_count = fail_count + 1 WHERE user_id=?", (user_id,))
            self.conn.commit()

    def reset_fail(self, user_id: str):
        """发送成功清零连续失败计数。"""
        with self._lock:
            self.conn.execute("UPDATE jian_prefs SET fail_count = 0 WHERE user_id=?", (user_id,))
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
