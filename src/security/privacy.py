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
# k78：用户可见文案的单一事实源（本模块只引常量，不再内联写文案）
from .account_copy import DATA_RETENTION_ACTION_NOTICE

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

    def delete_user_data(self, user_id: str) -> Dict[str, Any]:
        """Delete all data for a user (Right to be Forgotten / PIPL 第 47 条).

        k76：**改为委托 `storage.dao.purge_account_data()`**（唯一实现）。

        改前的问题（k72-A3）：本方法自己内联了 5 张表的删除（consultations /
        push_log / user_preferences / users / payments），而 `dao.cleanup_
        cancelled_accounts()` 另有 10 张表的清单 —— 两份清单都不含 zeri_plans、
        night_lamp、night_prefs、night_remember、jian_prefs、ming_saves、
        ming_quota、qian_saves、chat_quota、share_entries、persons 之外的
        头像/上传图/报告文件 ⇒「删除权」名不副实。现在删除清单**只有一份**
        （`storage/models.py::ACCOUNT_PURGE_TABLES` / `ACCOUNT_RETAIN_TABLES`）。

        返回：`{表名: 删除行数, ..., "user": n, "files": {...}, "retained": {...},
        "ok": bool, "failed_tables": {...}, "missing_tables": [...],
        "failed_files": [...]}` —— 保留 `"user"` 键（既有调用方与测试读它）。
        `payments` / `midas_orders` **不删**（依法留存），依据见返回值 `retained`。

        k79-M1：`ok` / `failed_tables` / `failed_files` 透传自
        `purge_account_data()` —— 调用端点据此**不得**在真失败时仍回"删除完成"
        （见 `api/user.py` 同级的两个删除端点）。
        """
        from ..storage.dao import purge_account_data

        conn = self._connect()
        try:
            stats = purge_account_data(conn, user_id)
            conn.commit()
            deleted: Dict[str, Any] = dict(stats["tables"])
            deleted["user"] = stats["tables"].get("users", 0)
            deleted["files"] = stats["files"]
            deleted["retained"] = stats["retained"]
            # k79-M1：让"真失败"能一路走到响应（改前这里就把失败信息丢掉了）
            deleted["ok"] = stats.get("ok", True)
            deleted["failed_tables"] = stats.get("failed_tables") or {}
            deleted["missing_tables"] = stats.get("missing_tables") or []
            deleted["failed_files"] = stats.get("failed_files") or []
            logger.info(
                "Deleted all data for user %s: %s",
                self.encryptor.encrypt_user_id(user_id), deleted,
            )
            return deleted
        except Exception as e:
            conn.rollback()
            logger.error("Failed to delete user data for %s: %s", user_id, str(e))
            raise
        finally:
            conn.close()

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
                # k78：与 account_copy 同源（改前写"（不可恢复）"，与"备份仍覆盖时可
                # 人工尝试找回"冲突；本仓统一口径是"不可自助恢复"）
                "action": DATA_RETENTION_ACTION_NOTICE,
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
