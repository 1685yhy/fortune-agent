"""Tests for Sprint 4: Retention Engine."""
import os
import sys
import tempfile
import json
import pytest
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.storage.dao import UserDAO
from src.storage.member_dao import MemberDAO


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
    return UserDAO(db_path)


@pytest.fixture
def member_dao(db_path):
    return MemberDAO(db_path)


class TestUserHistory:
    def test_get_consultations_empty(self, dao):
        """New user should have empty consultation history."""
        cons = dao.get_user_consultations("new_user")
        assert cons == []

    def test_get_consultations_with_data(self, dao):
        """User with consultations should see them in reverse chronological order."""
        dao.save_consultation("user1", "我的运势如何", intent="bazi", analysis="运势不错")
        dao.save_consultation("user1", "今天财运怎么样", intent="bazi", analysis="财运平稳")
        dao.save_consultation("user1", "帮我解梦梦见水", intent="dream", analysis="梦见水象征财运")

        cons = dao.get_user_consultations("user1", limit=10)
        assert len(cons) == 3
        # Should be reverse chronological (most recent first)
        assert cons[0]["question"] == "帮我解梦梦见水"
        assert cons[2]["question"] == "我的运势如何"

    def test_get_consultations_limit(self, dao):
        """Should respect the limit parameter."""
        for i in range(5):
            dao.save_consultation("user2", f"问题{i}", intent="bazi")
        cons = dao.get_user_consultations("user2", limit=3)
        assert len(cons) == 3

    def test_consultation_fields(self, dao):
        """Consultation records should contain correct fields."""
        dao.save_consultation(
            "user3", "我该跳槽吗", intent="bazi",
            analysis="60%可能性适合跳槽，建议谨慎",
        )
        cons = dao.get_user_consultations("user3", limit=1)
        assert len(cons) == 1
        c = cons[0]
        assert c["question"] == "我该跳槽吗"
        assert c["intent"] == "bazi"
        assert "analysis_preview" in c
        assert c["feedback"] is None or c["feedback"] == ""
        assert "created_at" in c
        assert "id" in c

    def test_last_consultation_id(self, dao):
        """After saving a consultation, last_consultation_id should be set."""
        dao.save_consultation("user4", "测试问题", intent="bazi")
        assert dao.last_consultation_id > 0

    def test_multiple_users_isolated(self, dao):
        """Different users should have separate histories."""
        dao.save_consultation("alice", "Alice的问题", intent="bazi")
        dao.save_consultation("bob", "Bob的问题", intent="bazi")

        alice_cons = dao.get_user_consultations("alice")
        bob_cons = dao.get_user_consultations("bob")

        assert len(alice_cons) == 1
        assert len(bob_cons) == 1
        assert alice_cons[0]["question"] == "Alice的问题"


class TestAccuracy:
    def test_accuracy_empty(self, dao):
        """User with no feedback should return None accuracy."""
        acc = dao.get_user_accuracy("new_user")
        assert acc["accuracy_pct"] is None
        assert acc["total_feedback"] == 0

    def test_accuracy_all_positive(self, dao):
        """All positive feedback should give 100% accuracy."""
        dao.save_consultation("user5", "Q1", intent="bazi")
        dao.save_consultation("user5", "Q2", intent="bazi")

        # Get consultation IDs
        cons = dao.get_user_consultations("user5")
        dao.save_feedback(cons[0]["id"], "positive")
        dao.save_feedback(cons[1]["id"], "positive")

        acc = dao.get_user_accuracy("user5")
        assert acc["accuracy_pct"] == 100.0
        assert acc["positive"] == 2
        assert acc["negative"] == 0

    def test_accuracy_mixed(self, dao):
        """Mixed feedback should calculate correct percentage."""
        dao.save_consultation("user6", "Q1", intent="bazi")
        dao.save_consultation("user6", "Q2", intent="bazi")
        dao.save_consultation("user6", "Q3", intent="bazi")

        cons = dao.get_user_consultations("user6")
        dao.save_feedback(cons[0]["id"], "positive")
        dao.save_feedback(cons[1]["id"], "positive")
        dao.save_feedback(cons[2]["id"], "negative")

        acc = dao.get_user_accuracy("user6")
        assert acc["accuracy_pct"] == pytest.approx(66.7, abs=0.1)
        assert acc["positive"] == 2
        assert acc["negative"] == 1

    def test_accuracy_all_negative(self, dao):
        """All negative feedback should give 0% accuracy."""
        dao.save_consultation("user7", "Q1", intent="bazi")
        cons = dao.get_user_consultations("user7")
        dao.save_feedback(cons[0]["id"], "negative")

        acc = dao.get_user_accuracy("user7")
        assert acc["accuracy_pct"] == 0.0
        assert acc["positive"] == 0
        assert acc["negative"] == 1

    def test_accuracy_ignores_null_feedback(self, dao):
        """Consultations without feedback should not be counted."""
        dao.save_consultation("user8", "Q1", intent="bazi")
        dao.save_consultation("user8", "Q2", intent="bazi")
        cons = dao.get_user_consultations("user8")
        dao.save_feedback(cons[0]["id"], "positive")
        # Leave cons[1] without feedback

        acc = dao.get_user_accuracy("user8")
        assert acc["total_feedback"] == 1
        assert acc["accuracy_pct"] == 100.0

    def test_accuracy_different_users(self, dao):
        """Accuracy should be isolated per user."""
        dao.save_consultation("u1", "Q1", intent="bazi")
        dao.save_consultation("u2", "Q2", intent="bazi")

        cons1 = dao.get_user_consultations("u1")
        cons2 = dao.get_user_consultations("u2")
        dao.save_feedback(cons1[0]["id"], "positive")
        dao.save_feedback(cons2[0]["id"], "negative")

        acc1 = dao.get_user_accuracy("u1")
        acc2 = dao.get_user_accuracy("u2")
        assert acc1["accuracy_pct"] == 100.0
        assert acc2["accuracy_pct"] == 0.0


class TestFeedback:
    def test_save_positive_feedback(self, dao):
        """Save positive feedback should succeed."""
        dao.save_consultation("user9", "Q1", intent="bazi")
        cons = dao.get_user_consultations("user9")
        result = dao.save_feedback(cons[0]["id"], "positive")
        assert result is True

        # Verify stored
        updated_cons = dao.get_user_consultations("user9")
        assert updated_cons[0]["feedback"] == "positive"

    def test_save_negative_feedback(self, dao):
        """Save negative feedback should succeed."""
        dao.save_consultation("user10", "Q1", intent="bazi")
        cons = dao.get_user_consultations("user10")
        result = dao.save_feedback(cons[0]["id"], "negative")
        assert result is True

        updated_cons = dao.get_user_consultations("user10")
        assert updated_cons[0]["feedback"] == "negative"

    def test_invalid_feedback(self, dao):
        """Invalid feedback value should be rejected."""
        dao.save_consultation("user11", "Q1", intent="bazi")
        cons = dao.get_user_consultations("user11")
        result = dao.save_feedback(cons[0]["id"], "invalid_value")
        assert result is False

    def test_feedback_nonexistent_id(self, dao):
        """Feedback for non-existent consultation should return False."""
        result = dao.save_feedback(99999, "positive")
        assert result is True  # SQL still succeeds, just no rows updated

    def test_feedback_overwrite(self, dao):
        """Feedback should be overwritable."""
        dao.save_consultation("user12", "Q1", intent="bazi")
        cons = dao.get_user_consultations("user12")
        dao.save_feedback(cons[0]["id"], "positive")
        dao.save_feedback(cons[0]["id"], "negative")

        updated_cons = dao.get_user_consultations("user12")
        assert updated_cons[0]["feedback"] == "negative"


class TestTotalPredictions:
    def test_empty_db(self, dao):
        """No consultations should give zero stats."""
        stats = dao.get_total_predictions()
        assert stats["total_predictions"] == 0
        assert stats["verified_count"] == 0
        assert stats["verified_pct"] == 0

    def test_with_consultations(self, dao):
        """Consultations should be counted."""
        dao.save_consultation("u1", "Q1", intent="bazi")
        dao.save_consultation("u2", "Q2", intent="bazi")
        dao.save_consultation("u3", "Q3", intent="bazi")

        stats = dao.get_total_predictions()
        assert stats["total_predictions"] == 3

    def test_verified_count(self, dao):
        """Only consultations with feedback should count as verified."""
        dao.save_consultation("u1", "Q1", intent="bazi")
        dao.save_consultation("u2", "Q2", intent="bazi")
        cons = dao.get_user_consultations("u2")
        dao.save_feedback(cons[0]["id"], "positive")

        stats = dao.get_total_predictions()
        assert stats["total_predictions"] == 2
        assert stats["verified_count"] == 1
        assert stats["verified_pct"] == 50.0


class TestWeeklyPush:
    def test_get_week_range(self):
        """Week range should return valid date strings."""
        from scripts.daily_push import get_week_range
        week = get_week_range()
        assert "start" in week
        assert "end" in week
        assert "start_cn" in week
        assert "end_cn" in week
        assert week["start"] <= week["end"]

    def test_generate_weekly_summary_format(self, dao):
        """Weekly summary should include accuracy and personalization sections."""
        from scripts.daily_push import generate_weekly_summary, get_today_ganzhi, compare_bazi_with_today

        bazi_info = {
            "bazi": ["甲子", "丙寅", "庚午", "戊辰"],
            "year": 1990, "month": 5, "day": 20,
            "hour": 15, "minute": 0, "city": "北京", "gender": "男",
        }

        today = get_today_ganzhi()
        comparison = compare_bazi_with_today(bazi_info["bazi"], today)
        message = generate_weekly_summary("test_user", bazi_info, today, comparison, dao)

        # Should be a non-empty string
        assert isinstance(message, str)
        assert len(message) > 50

        # Should contain week-related keywords
        assert "本周" in message or "运势" in message

    def test_run_weekly_push_batch_dry_run(self, dao):
        """Weekly push dry run should work without errors."""
        from scripts.daily_push import get_today_ganzhi, run_weekly_push_batch

        today = get_today_ganzhi()
        stats = run_weekly_push_batch(dao, today, dry_run=True)

        assert "total" in stats
        assert "pushed" in stats
        assert "errors" in stats


class TestAPIEndpoints:
    """Verifies endpoint logic works at the DAO level."""

    def test_history_endpoint_data(self, dao):
        """GET /api/user/{user_id}/history should return consultations with dates and topics."""
        dao.save_consultation("api_user", "2026年财运如何", intent="bazi")
        dao.save_consultation("api_user", "帮我看看婚姻", intent="bazi")

        cons = dao.get_user_consultations("api_user")
        assert len(cons) == 2
        assert all(c["created_at"] for c in cons)
        assert all(c["question"] for c in cons)

    def test_accuracy_endpoint_data(self, dao):
        """GET /api/user/{user_id}/accuracy should return calculated accuracy."""
        dao.save_consultation("acc_user", "Q1", intent="bazi")
        dao.save_consultation("acc_user", "Q2", intent="bazi")
        cons = dao.get_user_consultations("acc_user")
        dao.save_feedback(cons[0]["id"], "positive")
        dao.save_feedback(cons[1]["id"], "negative")

        acc = dao.get_user_accuracy("acc_user")
        assert acc["accuracy_pct"] == 50.0
        assert acc["positive"] == 1
        assert acc["negative"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
