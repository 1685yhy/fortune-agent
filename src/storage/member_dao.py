"""Membership and payment data access."""
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from .models import init_db


# Plan definitions
PLANS = {
    "free": {
        "label": "免费版",
        "price": 0,
        "queries_limit": 3,
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


class MemberDAO:
    """Membership data access layer."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        init_db(db_path)

    def _connect(self):
        return sqlite3.connect(self.db_path, timeout=10)

    def _ensure_free_membership(self, user_id: str):
        """Ensure a user always has a free membership row (default)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT user_id FROM memberships WHERE user_id = ?", (user_id,)
            ).fetchone()
            if not row:
                now = datetime.now().isoformat()
                conn.execute(
                    """INSERT INTO memberships
                       (user_id, plan, started_at, expires_at, queries_used, queries_limit, auto_renew)
                       VALUES (?, 'free', ?, ?, 0, 3, 0)""",
                    (user_id, now, now),
                )
                conn.commit()
        finally:
            conn.close()

    def get_membership(self, user_id: str) -> dict:
        """Get membership info for a user. Returns plan details with quota."""
        self._ensure_free_membership(user_id)
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT user_id, plan, started_at, expires_at,
                          queries_used, queries_limit, auto_renew, created_at
                   FROM memberships WHERE user_id = ?""",
                (user_id,),
            ).fetchone()
            if not row:
                return {"user_id": user_id, "plan": "free", "error": "not found"}

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

    def create_membership(self, user_id: str, plan: str, queries_limit: Optional[int] = None) -> bool:
        """Create or upgrade a membership."""
        if plan not in PLANS:
            return False

        if queries_limit is None:
            queries_limit = PLANS[plan]["queries_limit"]

        now = datetime.now()
        started_at = now.isoformat()

        period = PLANS[plan]["period_days"]
        expires_at = (now + timedelta(days=period)).isoformat() if period else None

        conn = self._connect()
        try:
            existing = conn.execute(
                "SELECT user_id FROM memberships WHERE user_id = ?", (user_id,)
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE memberships
                       SET plan=?, started_at=?, expires_at=?, queries_used=0,
                           queries_limit=?, auto_renew=0
                       WHERE user_id=?""",
                    (plan, started_at, expires_at, queries_limit, user_id),
                )
            else:
                conn.execute(
                    """INSERT INTO memberships
                       (user_id, plan, started_at, expires_at, queries_used, queries_limit, auto_renew)
                       VALUES (?, ?, ?, ?, 0, ?, 0)""",
                    (user_id, plan, started_at, expires_at, queries_limit),
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
        """Decrement quota (mark one query used). Returns True if successful."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT queries_limit, queries_used FROM memberships WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if not row:
                self._ensure_free_membership(user_id)
                row = conn.execute(
                    "SELECT queries_limit, queries_used FROM memberships WHERE user_id = ?",
                    (user_id,),
                ).fetchone()

            if row:
                limit, used = row
                if limit is not None and used >= limit:
                    # For free users, try reset
                    plan_row = conn.execute(
                        "SELECT plan FROM memberships WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()
                    if plan_row and plan_row[0] == "free":
                        conn.execute(
                            "UPDATE memberships SET queries_used=1 WHERE user_id=? AND plan='free'",
                            (user_id,),
                        )
                        conn.commit()
                        return True
                    return False

                conn.execute(
                    "UPDATE memberships SET queries_used = queries_used + 1 WHERE user_id = ?",
                    (user_id,),
                )
                conn.commit()
                return True
            return False
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

    def confirm_payment(self, payment_id: int, user_id: str, plan: str):
        """Confirm a payment and activate membership."""
        if plan not in PLANS:
            return

        queries_limit = PLANS[plan]["queries_limit"]
        now = datetime.now()
        started_at = now.isoformat()
        period = PLANS[plan]["period_days"]
        expires_at = (now + timedelta(days=period)).isoformat() if period else None

        conn = self._connect()
        try:
            conn.execute(
                "UPDATE payments SET status='paid' WHERE id=? AND user_id=?",
                (payment_id, user_id),
            )
            existing = conn.execute(
                "SELECT user_id FROM memberships WHERE user_id = ?", (user_id,)
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE memberships
                       SET plan=?, started_at=?, expires_at=?, queries_used=0,
                           queries_limit=?, auto_renew=0
                       WHERE user_id=?""",
                    (plan, started_at, expires_at, queries_limit, user_id),
                )
            else:
                conn.execute(
                    """INSERT INTO memberships
                       (user_id, plan, started_at, expires_at, queries_used, queries_limit, auto_renew)
                       VALUES (?, ?, ?, ?, 0, ?, 0)""",
                    (user_id, plan, started_at, expires_at, queries_limit),
                )
            conn.commit()
        finally:
            conn.close()
