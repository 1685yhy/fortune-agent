"""Membership and payment data access."""
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from .models import init_db, connect as db_connect


# Plan definitions
PLANS = {
    "free": {
        "label": "免费版",
        "price": 0,
        "queries_limit": 3,  # L5-2：与 _ensure_free_membership 行默认/日重置阈值一致（旧额度，仅非聊天功能用）
        "period_days": 1,  # daily reset for free
        "features": ["基础分析"],
    },
    "basic": {
        "label": "基础版",
        "price": 19.9,
        "queries_limit": 50,
        "period_days": 30,  # monthly
        "features": ["完整分析", "图表"],
    },
    "pro": {
        "label": "专业版",
        "price": 39.9,
        "queries_limit": 150,
        "period_days": 30,
        "features": ["完整分析", "图表", "PDF报告"],
    },
    "annual": {
        "label": "年度版",
        "price": 168,
        "queries_limit": 150,
        "period_days": 365,
        "features": ["专业版功能", "每周运势"],
    },
}

# L5-2 修复（跨档续费"取高者"）：档位排序——跨档购买时若现有更高档未过期，
# 保留高档（不降 plan）；同档/升档照常升级并延长。free < basic < pro < annual。
_PLAN_RANK = {"free": 0, "basic": 1, "pro": 2, "annual": 3}


class MemberDAO:
    """Membership data access layer."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        init_db(db_path)

    def _connect(self):
        return db_connect(self.db_path, timeout=10)

    def _ensure_free_membership(self, user_id: str):
        """R1-1（评测 T087/T088 修复·产品裁决「建档不初始化免费额度」）：空操作。

        旧实现：任何 get_membership/use_quota 调用都会为无行用户自动 INSERT
        免费档行——建档引导/自由对话/支付守卫等一切聊天路径都可能攻击性写入
        memberships（T087/T088 实锤：3→4 行，state_checks memberships_unchanged
        当场抓获）。修复：memberships 行只由显式支付/升级动作创建
        （create_membership / confirm_payment 保持 INSERT）；无行用户一律按
        免费档只读语义处理（get_membership 返回合成免费档、use_quota 空操作
        成功），**读路径与消费路径绝不落库**。保留签名以兼容潜在外部调用。
        """
        return None

    def get_membership(self, user_id: str) -> dict:
        """Get membership info for a user. Returns plan details with quota.

        R1-1：无行用户 → 返回免费档只读合成数据（不 INSERT——建档/对话流程
        零写入 memberships；`queries_used=0` 且 `started_at=None` 表示"从未
        开通"，与真实行可区分，消费端行为一致）。
        """
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT user_id, plan, started_at, expires_at,
                          queries_used, queries_limit, auto_renew, created_at
                   FROM memberships WHERE user_id = ?""",
                (user_id,),
            ).fetchone()
            if not row:
                _free = PLANS["free"]
                return {
                    "user_id": user_id,
                    "plan": "free",
                    "plan_label": _free["label"],
                    "started_at": None,
                    "expires_at": None,
                    "queries_used": 0,
                    "queries_limit": _free["queries_limit"],
                    "queries_remaining": _free["queries_limit"],
                    "auto_renew": False,
                    "features": _free["features"],
                }

            plan = row[1]
            expires_at = row[3]
            queries_limit = row[5]

            # Check if membership has expired
            now = datetime.now()
            if plan != "free" and expires_at:
                expires = datetime.fromisoformat(expires_at)
                if expires <= now:
                    # Free tier fallback
                    plan = "free"
                    queries_limit = 3

            # For free tier, check if daily quota should reset
            queries_used = row[4]
            if plan == "free":
                # Check if last used was today
                # Simple approach: store last_query_date or just reset if they've hit limit
                pass

            remaining = None
            if queries_limit is not None:
                remaining = max(0, queries_limit - queries_used)

            plan_info = PLANS.get(plan, PLANS["free"])

            return {
                "user_id": row[0],
                "plan": plan,
                "plan_label": plan_info["label"],
                "started_at": row[2],
                "expires_at": expires_at,
                "queries_used": queries_used,
                "queries_limit": queries_limit,
                "queries_remaining": remaining,
                "auto_renew": bool(row[6]),
                "features": plan_info["features"],
            }
        finally:
            conn.close()

    @staticmethod
    def _keep_higher_plan(purchased_plan: str, existing_plan: Optional[str],
                          existing_expires_at: Optional[str]) -> str:
        """跨档购买"取高者"（L5-2 修复）：现有档更高且未过期 → 保留现有档。

        购买低档时若现有高档未过期 → 返回现有档（不降 plan，购买天数照常入账）；
        否则返回购买档（升级/同档/已过期/无档一律按购买档生效）。
        """
        if not existing_plan or existing_plan == "free" or not existing_expires_at:
            return purchased_plan
        try:
            expires = datetime.fromisoformat(existing_expires_at)
        except (TypeError, ValueError):
            return purchased_plan
        if expires <= datetime.now():
            return purchased_plan
        if _PLAN_RANK.get(existing_plan, 0) > _PLAN_RANK.get(purchased_plan, 0):
            return existing_plan
        return purchased_plan

    @staticmethod
    def _compute_expiry(existing_plan: Optional[str], existing_expires_at: Optional[str],
                        period_days: Optional[int]) -> str:
        """到期时间计算（L5-2）：续费延长——付费会员未过期时从原到期日续加 period_days，
        否则（新开通/已过期/免费档）从现在起算 period_days。"""
        now = datetime.now()
        base = now
        if existing_plan and existing_plan != "free" and existing_expires_at:
            try:
                expires = datetime.fromisoformat(existing_expires_at)
                if expires > now:
                    base = expires
            except (TypeError, ValueError):
                pass
        return (base + timedelta(days=period_days)).isoformat() if period_days else None

    def create_membership(self, user_id: str, plan: str, queries_limit: Optional[int] = None,
                          period_days: Optional[int] = None) -> bool:
        """Create or upgrade a membership.

        period_days（L5-2）：显式指定（如季度 90/年度 365 商品）；缺省取 PLANS[plan]。
        到期时间：付费会员未过期时从原到期日续加（续费延长），否则从现在起算。
        跨档"取高者"（L5-2 修复）：购买低档时若现有高档未过期 → 保留高档
        （与 confirm_payment 同口径）。
        """
        if plan not in PLANS:
            return False

        if period_days is None:
            period_days = PLANS[plan]["period_days"]

        now = datetime.now()
        started_at = now.isoformat()

        conn = self._connect()
        try:
            existing = conn.execute(
                "SELECT plan, expires_at FROM memberships WHERE user_id = ?", (user_id,)
            ).fetchone()
            existing_plan = existing[0] if existing else None
            existing_expires_at = existing[1] if existing else None

            # L5-2 修复（跨档续费"取高者"）：生效档 = max(购买档, 未过期现有档)
            effective_plan = self._keep_higher_plan(
                plan, existing_plan, existing_expires_at)
            if queries_limit is None:
                queries_limit = PLANS[effective_plan]["queries_limit"]

            expires_at = self._compute_expiry(
                existing_plan, existing_expires_at, period_days)

            if existing:
                conn.execute(
                    """UPDATE memberships
                       SET plan=?, started_at=?, expires_at=?, queries_used=0,
                           queries_limit=?, auto_renew=0
                       WHERE user_id=?""",
                    (effective_plan, started_at, expires_at, queries_limit, user_id),
                )
            else:
                conn.execute(
                    """INSERT INTO memberships
                       (user_id, plan, started_at, expires_at, queries_used, queries_limit, auto_renew)
                       VALUES (?, ?, ?, ?, 0, ?, 0)""",
                    (user_id, effective_plan, started_at, expires_at, queries_limit),
                )
            conn.commit()
            return True
        finally:
            conn.close()

    def check_quota(self, user_id: str) -> bool:
        """Check if the user has remaining quota to make a query. True = can query."""
        membership = self.get_membership(user_id)
        limit = membership.get("queries_limit")
        used = membership.get("queries_used", 0)
        plan = membership.get("plan", "free")

        if limit is None:
            return True  # unlimited

        remaining = limit - used
        if remaining > 0:
            return True

        # Free tier: auto-reset daily
        if plan == "free":
            self._reset_daily_quota(user_id)
            membership = self.get_membership(user_id)
            remaining = (membership.get("queries_limit") or 3) - membership.get("queries_used", 0)
            return remaining > 0

        return False

    def _reset_daily_quota(self, user_id: str):
        """Reset daily quota for free users if needed."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT queries_used FROM memberships WHERE user_id = ? AND plan = 'free'",
                (user_id,),
            ).fetchone()
            if row and row[0] >= 3:
                conn.execute(
                    "UPDATE memberships SET queries_used=0 WHERE user_id=? AND plan='free'",
                    (user_id,),
                )
                conn.commit()
        finally:
            conn.close()

    def use_quota(self, user_id: str) -> bool:
        """Mark one query as used. Returns True if quota was consumed.

        R2-2（额度限流修复）：满额（used >= limit）不再重置——旧实现把 free
        用户的 queries_used 重置为 1（「免费用户永远能聊」软化设计），而聊天
        路径的 _consume_quota 在择日额度门（_check_quota）之前执行 → 重置后
        门检查 remaining = limit-1 > 0 → 门永不触发，免费用户可无限调引擎。
        修复：满额不 UPDATE、保持 used=limit、返回 False（未扣成）。三个
        调用方（handler._consume_quota / main.py / chat_stream.py）均忽略
        返回值，语义兼容（不新增依赖）。memberships 零写入红线延续：此处
        无 INSERT；未满扣减照常 +1。
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT queries_limit, queries_used FROM memberships WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if not row:
                # R1-1：无行用户视为免费档，消费空操作成功（不 INSERT——
                # 建档/对话流程零写入 memberships 的产品裁决；聊天额度由
                # chat_quota 治理，旧额度门只对已有行——显式支付创建的
                # 用户生效，无行用户永不因额度门被挡）。
                return True

            limit, used = row
            if limit is not None and used >= limit:
                # R2-2：满额不再重置——额度门（_check_quota）据此真触发
                return False

            conn.execute(
                "UPDATE memberships SET queries_used = queries_used + 1 WHERE user_id = ?",
                (user_id,),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def mark_payment_paid(self, payment_id: int, user_id: str) -> bool:
        """把订单直接标记为已支付（mock 支付模式；不激活会员）。"""
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE payments SET status='paid' WHERE id=? AND user_id=?",
                (payment_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_user_payments(self, user_id: str, limit: int = 50) -> list:
        """获取用户的支付/订单记录（新单在前，仅供本人查询）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT id, user_id, amount, plan, status, payment_method, created_at
                   FROM payments WHERE user_id = ? ORDER BY id DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "user_id": r[1],
                    "amount": r[2],
                    "plan": r[3],
                    "status": r[4],
                    "payment_method": r[5],
                    "created_at": r[6],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_user_purchase(self, user_id: str, product_id: str) -> Optional[dict]:
        """查询用户对某产品的成功购买记录（status=paid，无则 None）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT id, user_id, amount, plan, status, created_at
                   FROM payments
                   WHERE user_id = ? AND plan = ? AND status = 'paid'
                   ORDER BY id DESC LIMIT 1""",
                (user_id, product_id),
            ).fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "user_id": row[1],
                "amount": row[2],
                "plan": row[3],
                "status": row[4],
                "created_at": row[5],
            }
        finally:
            conn.close()

    def list_active_members(self) -> list:
        """List all users with non-free paid memberships that haven't expired."""
        conn = self._connect()
        try:
            now = datetime.now().isoformat()
            rows = conn.execute(
                """SELECT user_id, plan, started_at, expires_at, queries_used, queries_limit
                   FROM memberships
                   WHERE plan != 'free'
                     AND (expires_at IS NULL OR expires_at > ?)
                   ORDER BY plan, user_id""",
                (now,),
            ).fetchall()
            return [
                {
                    "user_id": r[0],
                    "plan": r[1],
                    "started_at": r[2],
                    "expires_at": r[3],
                    "queries_used": r[4],
                    "queries_limit": r[5],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_stats(self) -> dict:
        """Get membership statistics."""
        conn = self._connect()
        try:
            now = datetime.now().isoformat()

            total_members = conn.execute("SELECT COUNT(*) FROM memberships").fetchone()[0]
            active_paid = conn.execute(
                """SELECT COUNT(*) FROM memberships
                   WHERE plan != 'free'
                     AND (expires_at IS NULL OR expires_at > ?)""",
                (now,),
            ).fetchone()[0]

            # Plan breakdown
            plan_counts = {}
            for row in conn.execute(
                "SELECT plan, COUNT(*) FROM memberships GROUP BY plan"
            ).fetchall():
                plan_counts[row[0]] = row[1]

            # Revenue stats
            total_revenue = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'paid'"
            ).fetchone()[0]

            payment_count = conn.execute(
                "SELECT COUNT(*) FROM payments WHERE status = 'paid'"
            ).fetchone()[0]

            # Recent payments
            recent = conn.execute(
                """SELECT id, user_id, amount, plan, status, created_at
                   FROM payments ORDER BY created_at DESC LIMIT 10"""
            ).fetchall()
            recent_payments = [
                {
                    "id": r[0],
                    "user_id": r[1],
                    "amount": r[2],
                    "plan": r[3],
                    "status": r[4],
                    "created_at": r[5],
                }
                for r in recent
            ]

            return {
                "total_members": total_members,
                "active_paid_members": active_paid,
                "plan_breakdown": plan_counts,
                "total_revenue": total_revenue,
                "total_payments": payment_count,
                "recent_payments": recent_payments,
            }
        finally:
            conn.close()

    def create_payment(self, user_id: str, amount: float, plan: str, payment_method: str = "") -> int:
        """Record a new payment. Returns the payment ID."""
        conn = self._connect()
        try:
            cursor = conn.execute(
                "INSERT INTO payments (user_id, amount, plan, status, payment_method) VALUES (?, ?, ?, 'pending', ?)",
                (user_id, amount, plan, payment_method),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def confirm_payment(self, payment_id: int, user_id: str, plan: str,
                        period_days: Optional[int] = None):
        """Confirm a payment and activate membership.

        period_days（L5-2）：商品显式档期（如季度 90/年度 365），缺省取 PLANS[plan]；
        到期时间按续费延长计算（见 _compute_expiry）。
        跨档"取高者"（L5-2 修复）：购买低档时若现有高档未过期 → 保留高档
        （不降 plan，购买天数照常从原到期日延长）；购买高档 → 升级并延长。
        """
        if plan not in PLANS:
            return

        if period_days is None:
            period_days = PLANS[plan]["period_days"]
        now = datetime.now()
        started_at = now.isoformat()

        conn = self._connect()
        try:
            conn.execute(
                "UPDATE payments SET status='paid' WHERE id=? AND user_id=?",
                (payment_id, user_id),
            )
            existing = conn.execute(
                "SELECT plan, expires_at FROM memberships WHERE user_id = ?", (user_id,)
            ).fetchone()
            existing_plan = existing[0] if existing else None
            existing_expires_at = existing[1] if existing else None

            # L5-2 修复（跨档续费"取高者"）：生效档 = max(购买档, 未过期现有档)
            effective_plan = self._keep_higher_plan(
                plan, existing_plan, existing_expires_at)
            queries_limit = PLANS[effective_plan]["queries_limit"]

            expires_at = self._compute_expiry(
                existing_plan, existing_expires_at, period_days)

            if existing:
                conn.execute(
                    """UPDATE memberships
                       SET plan=?, started_at=?, expires_at=?, queries_used=0,
                           queries_limit=?, auto_renew=0
                       WHERE user_id=?""",
                    (effective_plan, started_at, expires_at, queries_limit, user_id),
                )
            else:
                conn.execute(
                    """INSERT INTO memberships
                       (user_id, plan, started_at, expires_at, queries_used, queries_limit, auto_renew)
                       VALUES (?, ?, ?, ?, 0, ?, 0)""",
                    (user_id, effective_plan, started_at, expires_at, queries_limit),
                )
            conn.commit()
        finally:
            conn.close()
