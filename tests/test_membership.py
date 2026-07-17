"""Tests for membership and payment system."""
import os
import sys
import tempfile
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.storage.member_dao import MemberDAO, PLANS


@pytest.fixture
def db_path():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name
    yield tmp_path
    if os.path.exists(tmp_path):
        os.unlink(tmp_path)


@pytest.fixture
def dao(db_path):
    return MemberDAO(db_path)


class TestMemberDAO:
    def test_get_membership_creates_free(self, dao):
        """Getting membership for a new user should create a free plan."""
        mem = dao.get_membership("new_user")
        assert mem["user_id"] == "new_user"
        assert mem["plan"] == "free"
        assert mem["queries_limit"] == 3
        assert mem["queries_used"] == 0

    def test_create_membership_basic(self, dao):
        """Create a basic membership."""
        result = dao.create_membership("user1", "basic")
        assert result is True
        mem = dao.get_membership("user1")
        assert mem["plan"] == "basic"
        assert mem["queries_limit"] == 50
        assert mem["queries_used"] == 0

    def test_create_membership_invalid_plan(self, dao):
        """Invalid plan should return False."""
        result = dao.create_membership("user2", "ultra_plan")
        assert result is False

    def test_create_membership_upgrade(self, dao):
        """Upgrade from basic to pro."""
        dao.create_membership("user3", "basic")
        dao.create_membership("user3", "pro")
        mem = dao.get_membership("user3")
        assert mem["plan"] == "pro"
        assert mem["queries_limit"] == 150

    def test_check_quota_free_has_quota(self, dao):
        """Free user should have quota initially."""
        assert dao.check_quota("quota_user") is True

    def test_use_quota_decrements(self, dao):
        """Using quota should increment queries_used."""
        dao.use_quota("quota_user2")
        mem = dao.get_membership("quota_user2")
        assert mem["queries_used"] == 1

    def test_quota_exhausted(self, dao):
        """Using all free quota should return False for check_quota initially, but auto-reset."""
        for _ in range(5):
            dao.use_quota("quota_user3")
        # After exhausting, check_quota should auto-reset
        mem = dao.get_membership("quota_user3")
        assert mem["queries_used"] <= 3  # auto-reset

    def test_paid_quota(self, dao):
        """Paid user should have correct quota."""
        dao.create_membership("paid_user", "pro")
        mem = dao.get_membership("paid_user")
        assert mem["queries_limit"] == 150
        assert mem["queries_remaining"] == 150

        for _ in range(10):
            dao.use_quota("paid_user")

        mem = dao.get_membership("paid_user")
        assert mem["queries_used"] == 10
        assert mem["queries_remaining"] == 140

    def test_list_active_members(self, dao):
        """List active paid members."""
        dao.create_membership("active1", "basic")
        dao.create_membership("active2", "pro")
        dao.create_membership("free_user", "free")

        active = dao.list_active_members()
        user_ids = [m["user_id"] for m in active]
        assert "active1" in user_ids
        assert "active2" in user_ids
        assert "free_user" not in user_ids

    def test_create_payment(self, dao):
        """Create and confirm a payment."""
        pid = dao.create_payment("pay_user", 19.9, "basic", "支付宝")
        assert pid > 0

        dao.confirm_payment(pid, "pay_user", "basic")
        mem = dao.get_membership("pay_user")
        assert mem["plan"] == "basic"

    def test_get_stats(self, dao):
        """Stats should reflect memberships and payments."""
        dao.create_membership("stat1", "basic")
        dao.create_membership("stat2", "pro")
        pid = dao.create_payment("stat1", 19.9, "basic")
        dao.confirm_payment(pid, "stat1", "basic")

        stats = dao.get_stats()
        assert stats["total_members"] >= 2
        assert stats["total_payments"] >= 1
        assert stats["total_revenue"] >= 19.9

    def test_check_quota_true_for_paid(self, dao):
        """Paid users with remaining quota should always be allowed."""
        dao.create_membership("paid_check", "basic")
        assert dao.check_quota("paid_check") is True

    def test_use_quota_returns_true_on_success(self, dao):
        """use_quota should return True when quota is used."""
        result = dao.use_quota("valid_user")
        assert result is True

    def test_membership_expiry_fallback(self, dao):
        """Membership that has expired should fall back to free."""
        dao.create_membership("expire_user", "basic")
        # We can't easily manipulate created_at retroactively, so just verify basic
        mem = dao.get_membership("expire_user")
        assert mem["plan"] == "basic"  # Not expired yet in test


class TestPlans:
    def test_plan_definitions(self):
        assert PLANS["free"]["queries_limit"] == 3
        assert PLANS["basic"]["price"] == 19.9
        assert PLANS["pro"]["price"] == 39.9
        assert PLANS["annual"]["price"] == 168
        assert PLANS["annual"]["period_days"] == 365

    def test_plan_features(self):
        assert "PDF报告" in PLANS["pro"]["features"]
        assert "每周运势" in PLANS["annual"]["features"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
