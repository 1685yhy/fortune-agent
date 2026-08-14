"""择吉日存储。表 zeri_plans(择日计划) / zeri_prefs(订阅偏好) / zeri_quota(换一批额度)。

- zeri_plans: 一次选定的吉日计划(id PK AUTOINCREMENT),card_json 存吉日卡完整数据,
  items_json 存办事清单 items;status=active/cancelled;索引 (user_id, lucky_date)。
- zeri_prefs: reminder_enabled 开关/fail_count 连续失败计数/bound_status 绑定态;
  openid 复用 jian_prefs.mp_openid(同一服务号,不重复存),绑定态读取以 jian_prefs 为准。
- zeri_quota: 换一批每日计数,PK(user_id, day);免费每日 3 次,会员不限(由 API 层判定)。
"""
import json
import time
import threading
from typing import Optional


class ZeriDAO:
    def __init__(self, conn):
        self.conn = conn
        # 写锁: 连接为进程级共享(check_same_thread=False),upsert/清单项修改等
        # 读-改-写序列需加锁,否则并发请求可能互相覆盖(与 jian_dao 同款修复)
        self._lock = threading.Lock()
        self.conn.execute("""CREATE TABLE IF NOT EXISTS zeri_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            scene TEXT NOT NULL,
            lucky_date TEXT NOT NULL,
            card_json TEXT NOT NULL,
            items_json TEXT NOT NULL,
            plan_type TEXT NOT NULL DEFAULT 'free',
            reminder_enabled INTEGER DEFAULT 0,
            remind_sent_d1 INTEGER DEFAULT 0,
            remind_sent_d0 INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at REAL
        )""")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_zeri_plans_user_date ON zeri_plans (user_id, lucky_date)")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS zeri_prefs (
            user_id TEXT PRIMARY KEY,
            reminder_enabled INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0,
            bound_status TEXT DEFAULT 'unbound',
            updated_at REAL
        )""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS zeri_quota (
            user_id TEXT NOT NULL,
            day TEXT NOT NULL,
            refresh_count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, day)
        )""")
        self.conn.commit()

    # ─────────────────────────── 计划 zeri_plans ───────────────────────────

    def upsert_plan(self, user_id: str, scene: str, lucky_date: str,
                    card: dict, items: list, plan_type: str,
                    reminder_enabled: int = 0) -> int:
        """选定落库。同一 (user_id, scene, lucky_date) 的 active 计划存在则更新(不重复建),
        否则插入新计划。返回 plan_id。"""
        card_json = json.dumps(card, ensure_ascii=False)
        items_json = json.dumps(items, ensure_ascii=False)
        with self._lock:
            row = self.conn.execute(
                "SELECT id FROM zeri_plans WHERE user_id=? AND scene=? AND lucky_date=?"
                " AND status='active'",
                (user_id, scene, lucky_date)).fetchone()
            now = time.time()
            if row:
                self.conn.execute(
                    "UPDATE zeri_plans SET card_json=?, items_json=?, plan_type=?, created_at=?"
                    " WHERE id=?",
                    (card_json, items_json, plan_type, now, row[0]))
                self.conn.commit()
                return row[0]
            cur = self.conn.execute(
                """INSERT INTO zeri_plans (user_id, scene, lucky_date, card_json, items_json,
                   plan_type, reminder_enabled, remind_sent_d1, remind_sent_d0, status, created_at)
                   VALUES (?,?,?,?,?,?,?,0,0,'active',?)""",
                (user_id, scene, lucky_date, card_json, items_json, plan_type,
                 reminder_enabled, now))
            self.conn.commit()
            return cur.lastrowid

    def _row_to_plan(self, row) -> dict:
        cols = [d[0] for d in self.conn.execute("SELECT * FROM zeri_plans").description]
        p = dict(zip(cols, row))
        try:
            p["card"] = json.loads(p.pop("card_json"))
        except Exception:
            p["card"] = {}
        try:
            p["items"] = json.loads(p.pop("items_json"))
        except Exception:
            p["items"] = []
        return p

    def get_plan_by_id(self, plan_id: int) -> Optional[dict]:
        """按 id 取计划(不校验归属,归属判定由 API 层做 403/404 区分)。"""
        row = self.conn.execute("SELECT * FROM zeri_plans WHERE id=?",
                                (plan_id,)).fetchone()
        return self._row_to_plan(row) if row else None

    def get_plan(self, user_id: str, plan_id: int) -> Optional[dict]:
        """归属校验取计划:非本人一律返回 None(接口安全红线)。"""
        row = self.conn.execute("SELECT * FROM zeri_plans WHERE id=? AND user_id=?",
                                (plan_id, user_id)).fetchone()
        return self._row_to_plan(row) if row else None

    def list_plans(self, user_id: str, limit: Optional[int] = None) -> list:
        """历史列表(最新在前,仅 active;未来 cancel 端点引入后历史不再混入)。
        limit=None 全量(会员档);limit=N 免费档近 N 条。"""
        if limit is not None:
            rows = self.conn.execute(
                "SELECT * FROM zeri_plans WHERE user_id=? AND status='active'"
                " ORDER BY id DESC LIMIT ?",
                (user_id, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM zeri_plans WHERE user_id=? AND status='active'"
                " ORDER BY id DESC",
                (user_id,)).fetchall()
        return [self._row_to_plan(r) for r in rows]

    def count_plans(self, user_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM zeri_plans WHERE user_id=? AND status='active'",
            (user_id,)).fetchone()
        return row[0]

    def update_item(self, plan_id: int, item_idx: int, patch: dict) -> Optional[list]:
        """更新清单某一项(done/note 等)。item_idx 越界抛 IndexError。
        返回更新后的完整 items 列表;计划不存在返回 None。"""
        with self._lock:
            row = self.conn.execute("SELECT items_json FROM zeri_plans WHERE id=?",
                                    (plan_id,)).fetchone()
            if not row:
                return None
            try:
                items = json.loads(row[0])
            except Exception:
                items = []
            if item_idx < 0 or item_idx >= len(items):
                raise IndexError(item_idx)
            items[item_idx].update(patch)
            self.conn.execute(
                "UPDATE zeri_plans SET items_json=? WHERE id=?",
                (json.dumps(items, ensure_ascii=False), plan_id))
            self.conn.commit()
            return items

    def set_reminder(self, user_id: str, plan_id: int, enabled: int) -> bool:
        """提醒订阅开关(主动同意制:enabled=1 即用户同意落库排期)。返回是否命中本人计划。"""
        cur = self.conn.execute(
            "UPDATE zeri_plans SET reminder_enabled=? WHERE id=? AND user_id=?",
            (enabled, plan_id, user_id))
        self.conn.commit()
        return cur.rowcount > 0

    def set_remind_sent(self, user_id: str, plan_id: int, d1_or_d0: str) -> bool:
        """标记提醒已发送(d1=提前1天 / d0=当天),供排期去重。非法值直接拒绝,不落库。"""
        if d1_or_d0 not in ("d1", "d0"):
            return False
        col = "remind_sent_d1" if d1_or_d0 == "d1" else "remind_sent_d0"
        cur = self.conn.execute(
            f"UPDATE zeri_plans SET {col}=1 WHERE id=? AND user_id=?", (plan_id, user_id))
        self.conn.commit()
        return cur.rowcount > 0

    # ─────────────────────────── 换一批额度 zeri_quota ───────────────────────────

    def get_refresh_count(self, user_id: str, day: str) -> int:
        row = self.conn.execute(
            "SELECT refresh_count FROM zeri_quota WHERE user_id=? AND day=?",
            (user_id, day)).fetchone()
        return row[0] if row else 0

    def bump_refresh(self, user_id: str, day: str, limit: Optional[int] = None) -> tuple:
        """换一批计数 +1。返回 (allowed, count):
        - limit=None(会员/体验模式):不限,恒放行
        - limit=N(免费每日上限):已达上限则不放行(计数不变),由调用方返回 429
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT refresh_count FROM zeri_quota WHERE user_id=? AND day=?",
                (user_id, day)).fetchone()
            count = row[0] if row else 0
            if limit is not None and count >= limit:
                return False, count
            count += 1
            self.conn.execute(
                """INSERT INTO zeri_quota (user_id, day, refresh_count) VALUES (?,?,?)
                   ON CONFLICT(user_id, day) DO UPDATE SET refresh_count=excluded.refresh_count""",
                (user_id, day, count))
            self.conn.commit()
            return True, count

    # ─────────────────────────── 偏好 zeri_prefs ───────────────────────────

    def get_pref(self, user_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM zeri_prefs WHERE user_id=?",
                                (user_id,)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM zeri_prefs").description]
        return dict(zip(cols, row))

    def upsert_pref(self, user_id: str, patch: dict):
        """偏好合并写(读当前行→合并→写回,锁内完成防并发丢字段,同 jian_dao 模式)。"""
        with self._lock:
            cur = self.conn.execute("SELECT * FROM zeri_prefs WHERE user_id=?",
                                    (user_id,)).fetchone()
            cols = [d[0] for d in self.conn.execute("SELECT * FROM zeri_prefs").description]
            data = dict(zip(cols, cur)) if cur else {}
            data.update(patch)
            data["user_id"] = user_id
            data["updated_at"] = time.time()
            defaults = {"reminder_enabled": 0, "fail_count": 0, "bound_status": "unbound"}
            for k, v in defaults.items():
                data.setdefault(k, v)
            self.conn.execute(
                """INSERT INTO zeri_prefs (user_id, reminder_enabled, fail_count,
                   bound_status, updated_at)
                   VALUES (:user_id,:reminder_enabled,:fail_count,:bound_status,:updated_at)
                   ON CONFLICT(user_id) DO UPDATE SET
                   reminder_enabled=excluded.reminder_enabled, fail_count=excluded.fail_count,
                   bound_status=excluded.bound_status, updated_at=excluded.updated_at""",
                data)
            self.conn.commit()

    def get_jian_bound_status(self, user_id: str) -> Optional[str]:
        """绑定态复用 jian_prefs(同一服务号,openid 不重复存)。无行/表缺失 → None。"""
        try:
            row = self.conn.execute(
                "SELECT bound_status FROM jian_prefs WHERE user_id=?", (user_id,)).fetchone()
            return row[0] if row else None
        except Exception:
            return None
