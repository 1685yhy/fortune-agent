"""Privacy controls for Fortune Agent.

Implements:
- Data retention policy (auto-delete inactive users after 180 days)
- User data export endpoint
- User data deletion endpoint
- Right to be forgotten compliance (PIPL)
"""
import os
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from pathlib import Path

from .encryption import DataEncryptor

logger = logging.getLogger(__name__)

# PIPL (个人信息保护法) compliance disclaimer
PIPL_DISCLAIMER = (
    "免责声明：本平台提供的内容仅供娱乐参考，不构成任何形式的专业建议。"
    "用户提供的个人信息将按照《个人信息保护法》严格保护。"
)

# Data retention: 180 days of inactivity
INACTIVE_DAYS = 180


class PrivacyManager:
    """Privacy controls manager.

    Handles data retention, user data export, and deletion
    in compliance with China's PIPL (个人信息保护法).
    """

    def __init__(self, db_path: str, encryptor: Optional[DataEncryptor] = None):
        self.db_path = db_path
        self.encryptor = encryptor or DataEncryptor()
        self._inactive_days = INACTIVE_DAYS

    def _connect(self):
        """Get database connection."""
        return sqlite3.connect(self.db_path)

    def _decrypt_field(self, text: Optional[str]) -> Optional[str]:
        """导出时解密敏感字段；解密失败按明文返回（兼容旧数据）。"""
        if not text:
            return text
        if not text.lstrip().startswith("{") and ":" in text:
            try:
                decrypted = self.encryptor.decrypt(text)
                if decrypted is not None:
                    return decrypted
            except Exception:
                pass
        return text

    def get_inactive_users(self, days: Optional[int] = None) -> List[Dict[str, Any]]:
        """Find users inactive for the specified number of days.

        Args:
            days: Days of inactivity threshold (default: INACTIVE_DAYS)

        Returns:
            List of dicts with user_id, last_active, days_inactive
        """
        threshold_days = days or self._inactive_days
        cutoff = (datetime.now(timezone.utc) - timedelta(days=threshold_days)).isoformat()

        conn = self._connect()
        rows = conn.execute(
            """SELECT user_id, updated_at
               FROM users
               WHERE updated_at < ?""",
            (cutoff,),
        ).fetchall()
        conn.close()

        results = []
        for row in rows:
            user_id, updated_at = row
            if updated_at:
                try:
                    last_active = datetime.fromisoformat(updated_at)
                    if last_active.tzinfo is None:
                        last_active = last_active.replace(tzinfo=timezone.utc)
                    days_inactive = (datetime.now(timezone.utc) - last_active).days
                except Exception:
                    days_inactive = threshold_days

                results.append({
                    "user_id": user_id,
                    "last_active": updated_at,
                    "days_inactive": days_inactive,
                })

        return results

    def delete_user_data(self, user_id: str) -> Dict[str, int]:
        """Delete all data for a user (Right to be Forgotten).

        Deletes from:
        - users table
        - consultations table
        - push_log table
        - member_dao tables

        Args:
            user_id: The user identifier

        Returns:
            Dict with counts of deleted records per table
        """
        conn = self._connect()
        deleted = {}

        try:
            # Delete from consultations
            deleted["consultations"] = conn.execute(
                "DELETE FROM consultations WHERE user_id = ?", (user_id,)
            ).rowcount

            # Delete from push_log
            try:
                deleted["push_log"] = conn.execute(
                    "DELETE FROM push_log WHERE user_id = ?", (user_id,)
                ).rowcount
            except Exception:
                deleted["push_log"] = 0

            # Delete from user_preferences
            try:
                deleted["preferences"] = conn.execute(
                    "DELETE FROM user_preferences WHERE user_id = ?", (user_id,)
                ).rowcount
            except Exception:
                deleted["preferences"] = 0

            # Delete main user record
            deleted["user"] = conn.execute(
                "DELETE FROM users WHERE user_id = ?", (user_id,)
            ).rowcount

            # Delete from members table
            try:
                deleted["members"] = conn.execute(
                    "DELETE FROM members WHERE user_id = ?", (user_id,)
                ).rowcount
            except Exception:
                deleted["members"] = 0

            # Delete from payments table
            try:
                deleted["payments"] = conn.execute(
                    "DELETE FROM payments WHERE user_id = ?", (user_id,)
                ).rowcount
            except Exception:
                deleted["payments"] = 0

            # Delete from conversation_memory
            try:
                from ..storage.conversation_memory import ConversationMemory
                memory = ConversationMemory(self.db_path)
                memory.clear_user_memory(user_id)
                deleted["memory"] = 1
            except Exception:
                deleted["memory"] = 0

            # Delete L3 user memory files (方案 §5.5 隐私：注销时记忆一并删除)
            try:
                from ..memory.user_memory import UserMemory
                um = UserMemory()
                entries_removed = um.clear_entries(user_id)
                file_removed = um.clear_all(user_id)
                deleted["l3_memory"] = entries_removed + file_removed
            except Exception:
                deleted["l3_memory"] = 0

            conn.commit()
            logger.info(
                "Deleted all data for user %s: %s",
                self.encryptor.encrypt_user_id(user_id), deleted,
            )

        except Exception as e:
            conn.rollback()
            logger.error("Failed to delete user data for %s: %s", user_id, str(e))
            raise
        finally:
            conn.close()

        return deleted

    def export_user_data(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Export all data for a user (data portability).

        Args:
            user_id: The user identifier

        Returns:
            Dict with all user data, or None if user not found
        """
        conn = self._connect()
        try:
            # Get user profile
            user_row = conn.execute(
                "SELECT user_id, bazi_info, created_at, updated_at, "
                "push_enabled, push_time FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()

            if not user_row:
                return None

            # Get consultations
            cons_rows = conn.execute(
                "SELECT id, question, intent, chart_data, analysis, feedback, created_at "
                "FROM consultations WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()

            # Get push logs
            try:
                push_rows = conn.execute(
                    "SELECT id, push_date, message, success, created_at "
                    "FROM push_log WHERE user_id = ? ORDER BY created_at DESC",
                    (user_id,),
                ).fetchall()
            except Exception:
                push_rows = []

            # Get membership info
            try:
                member_row = conn.execute(
                    "SELECT plan, queries_limit, queries_used, queries_reset_at, "
                    "member_since, expires_at FROM members WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
            except Exception:
                member_row = None

            # Build export data
            export = {
                "export_time": datetime.now(timezone.utc).isoformat(),
                "platform": "易理明灯 Fortune Agent",
                "user_info": {
                    "user_id": user_id,
                    "has_bazi": bool(user_row[1]),
                    "created_at": user_row[2],
                    "last_active": user_row[3],
                    "push_enabled": bool(user_row[4]),
                    "push_time": user_row[5],
                },
                "consultations": [
                    {
                        "id": r[0],
                        "question": self._decrypt_field(r[1]),
                        "intent": r[2],
                        "feedback": r[5],
                        "created_at": r[6],
                    }
                    for r in cons_rows
                ],
                "total_consultations": len(cons_rows),
                "push_logs": [
                    {
                        "id": r[0],
                        "date": r[1],
                        "preview": (r[2] or "")[:100],
                        "success": bool(r[3]),
                        "created_at": r[4],
                    }
                    for r in push_rows
                ],
                "membership": None,
            }

            if member_row:
                export["membership"] = {
                    "plan": member_row[0],
                    "queries_limit": member_row[1],
                    "queries_used": member_row[2],
                    "member_since": member_row[4],
                    "expires_at": member_row[5],
                }

            return export

        except Exception as e:
            logger.error("Failed to export user data for %s: %s", user_id, str(e))
            raise
        finally:
            conn.close()

    def anonymize_user_data(self, user_id: str) -> bool:
        """Anonymize user data rather than fully deleting.

        Keeps aggregated stats but removes PII.
        Used when retention policy requires keeping some data.
        """
        conn = self._connect()
        try:
            # Remove bazi_info (PII)
            conn.execute(
                "UPDATE users SET bazi_info = NULL, updated_at = ? WHERE user_id = ?",
                (datetime.now().isoformat(), user_id),
            )

            # Remove names from chat history
            # (question field contains user questions; we keep the records
            # but anonymize the content)
            conn.execute(
                "UPDATE consultations SET question = '[已匿名]' WHERE user_id = ?",
                (user_id,),
            )

            conn.commit()
            logger.info(
                "Anonymized data for user %s",
                self.encryptor.encrypt_user_id(user_id),
            )
            return True

        except Exception as e:
            conn.rollback()
            logger.error("Failed to anonymize user data: %s", str(e))
            return False
        finally:
            conn.close()

    def cleanup_inactive_users(self, dry_run: bool = True) -> Dict[str, Any]:
        """Delete or anonymize data for inactive users.

        Args:
            dry_run: If True, only report what would be deleted

        Returns:
            Stats about cleanup operation
        """
        inactive = self.get_inactive_users()
        stats = {
            "total_inactive": len(inactive),
            "deleted": 0,
            "anonymized": 0,
            "errors": 0,
            "dry_run": dry_run,
            "users": [],
        }

        for user_info in inactive:
            user_id = user_info["user_id"]
            if dry_run:
                stats["users"].append({
                    "user_id": user_id,
                    "days_inactive": user_info["days_inactive"],
                    "action": "would_delete",
                })
                continue

            try:
                result = self.delete_user_data(user_id)
                stats["deleted"] += 1
                stats["users"].append({
                    "user_id": user_id,
                    "days_inactive": user_info["days_inactive"],
                    "action": "deleted",
                    "records": result,
                })
                logger.info("Cleaned up inactive user %s (inactive %d days)",
                          user_id, user_info["days_inactive"])
            except Exception as e:
                stats["errors"] += 1
                logger.error("Failed to cleanup user %s: %s", user_id, str(e))

        return stats

    def get_data_retention_info(self) -> Dict[str, Any]:
        """Get data retention policy information."""
        inactive_days = self._inactive_days
        inactive = self.get_inactive_users()

        return {
            "policy": {
                "inactive_threshold_days": inactive_days,
                "inactive_threshold_display": f"{inactive_days}天未活跃",
                "action": "数据删除（不可恢复）",
                "compliance": "《中华人民共和国个人信息保护法》第47条",
                "last_updated": datetime.now(timezone.utc).isoformat(),
            },
            "current_status": {
                "total_inactive_users": len(inactive),
                "oldest_inactive_user": min(
                    (u["days_inactive"] for u in inactive),
                    default=0,
                ),
            },
        }

    def get_disclaimer(self) -> str:
        """Get the PIPL disclaimer text for embedding in responses."""
        return PIPL_DISCLAIMER
