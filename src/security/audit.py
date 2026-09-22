"""Audit logging module for Fortune Agent.

Provides:
- Logging of all sensitive operations
- Structured audit log format
- Suspicious pattern detection and alerting
- Log rotation and management
"""
import os
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Callable
from functools import wraps

from src.security.log_redact import redact

logger = logging.getLogger(__name__)

# Default audit log path
DEFAULT_LOG_PATH = "/opt/fortune-agent/logs/audit.log"


class AuditLogger:
    """Structured audit logger for sensitive operations.

    Logs all security-relevant events with consistent format:
    timestamp, user_id, action, ip, user_agent, result, details

    Supports:
    - Suspicious pattern detection
    - Configurable log output
    - Decorator for easy integration
    """

    # Actions that require audit logging
    SENSITIVE_ACTIONS = {
        "data_access": "用户数据访问",
        "data_export": "用户数据导出",
        "data_deletion": "用户数据删除",
        "data_anonymization": "用户数据匿名化",
        "login": "登录",
        "login_failed": "登录失败",
        "token_refresh": "令牌刷新",
        "token_revoke": "令牌撤销",
        "api_key_usage": "API密钥使用",
        "rate_limit_exceeded": "速率限制超限",
        "attack_detected": "攻击检测",
        "admin_action": "管理员操作",
        "payment": "支付操作",
        "membership_change": "会员变更",
        "password_change": "密码变更",
        "data_retention_cleanup": "数据保留清理",
    }

    # Suspicious patterns to alert on
    SUSPICIOUS_PATTERNS = {
        "rapid_deletions": {
            "action": "data_deletion",
            "threshold": 5,  # More than 5 deletions in window
            "window": 300,   # Within 5 minutes
            "severity": "high",
            "description": "快速删除操作",
        },
        "mass_exports": {
            "action": "data_export",
            "threshold": 10,  # More than 10 exports in window
            "window": 600,   # Within 10 minutes
            "severity": "high",
            "description": "批量数据导出",
        },
        "failed_logins": {
            "action": "login_failed",
            "threshold": 5,   # More than 5 failures in window
            "window": 300,   # Within 5 minutes
            "severity": "medium",
            "description": "多次登录失败",
        },
        "attack_wave": {
            "action": "attack_detected",
            "threshold": 10,  # More than 10 attacks in window
            "window": 300,   # Within 5 minutes
            "severity": "critical",
            "description": "攻击波检测",
        },
    }

    def __init__(self, log_path: Optional[str] = None):
        self.log_path = log_path or os.getenv("AUDIT_LOG_PATH", DEFAULT_LOG_PATH)
        self._ensure_log_dir()

        # Suspicious pattern tracking
        self._action_timeline: Dict[str, list] = {}
        self._alert_handlers: list = []

        # Configure audit file handler
        self._setup_file_handler()

    def _ensure_log_dir(self):
        """Ensure log directory exists. Falls back to local logs/ if permissions fail."""
        log_dir = Path(self.log_path).parent
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, FileNotFoundError, OSError):
            # Fallback to local logs directory
            fallback_dir = Path.cwd() / "logs"
            fallback_dir.mkdir(parents=True, exist_ok=True)
            self.log_path = str(fallback_dir / "audit.log")
            logger.warning("Audit log fallback to: %s", self.log_path)

    def _setup_file_handler(self):
        """Set up file handler for audit logging."""
        try:
            # Create audit-specific logger
            self._audit_logger = logging.getLogger("audit")
            self._audit_logger.setLevel(logging.INFO)
            self._audit_logger.propagate = False

            # File handler with rotation
            from logging.handlers import RotatingFileHandler
            handler = RotatingFileHandler(
                self.log_path,
                maxBytes=100 * 1024 * 1024,  # 100 MB
                backupCount=10,
                encoding="utf-8",
            )

            # JSON formatter
            formatter = logging.Formatter(
                "%(message)s"  # We'll format as JSON ourselves
            )
            handler.setFormatter(formatter)
            self._audit_logger.addHandler(handler)

        except Exception as e:
            logger.warning("Could not set up audit file handler: %s", str(e))
            self._audit_logger = None

    def log(
        self,
        action: str,
        user_id: str = "anonymous",
        ip: str = "",
        user_agent: str = "",
        result: str = "success",
        details: Optional[Dict[str, Any]] = None,
    ):
        """Log an auditable event.

        Args:
            action: The action being logged (use SENSITIVE_ACTIONS keys)
            user_id: User identifier (prefer hashed ID)
            ip: Client IP address
            user_agent: Client user agent string
            result: 'success' or 'failure'
            details: Additional context data
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "timestamp_epoch": time.time(),
            "user_id": user_id,
            "action": action,
            "action_label": self.SENSITIVE_ACTIONS.get(action, action),
            "ip": ip,
            "user_agent": user_agent[:200] if user_agent else "",
            "result": result,
            "details": details or {},
        }

        # Write to audit file
        line = json.dumps(entry, ensure_ascii=False)
        if self._audit_logger:
            self._audit_logger.info(line)

        # Also log to main logger at INFO level
        logger.info("[AUDIT] %s | user=%s | action=%s | ip=%s | result=%s",
                   entry["timestamp"], user_id, action, ip, result)

        # Check for suspicious patterns
        self._check_suspicious(action, user_id)

    def data_access(self, user_id: str, accessed_by: str, ip: str, data_type: str):
        """Log data access event."""
        self.log(
            action="data_access",
            user_id=user_id,
            ip=ip,
            details={
                "accessed_by": accessed_by,
                "data_type": data_type,
            },
        )

    def data_export(self, user_id: str, ip: str, user_agent: str = ""):
        """Log data export event."""
        self.log(
            action="data_export",
            user_id=user_id,
            ip=ip,
            user_agent=user_agent,
        )

    def data_deletion(self, user_id: str, ip: str, deleted_by: str = "user"):
        """Log data deletion event."""
        self.log(
            action="data_deletion",
            user_id=user_id,
            ip=ip,
            details={"deleted_by": deleted_by},
        )

    def attack_detected(self, attack_type: str, user_id: str, ip: str, input_preview: str):
        """Log attack detection event.

        k86 必修2：`input_preview` **在唯一出口处脱敏**（不再 `[:100]` 截断明文）。
        审计日志每行还带 user_id / IP / User-Agent，若再带用户正文片段，等于把
        对话与身份一起长期留在一个**无时间上限**的文件里（audit.log 按容量滚动）。
        调用方可直接传**原文**（长度/指纹才准）；此处统一转成 `[len=.. h=..]`。
        """
        self.log(
            action="attack_detected",
            user_id=user_id,
            ip=ip,
            result="blocked",
            details={
                "attack_type": attack_type,
                "input_preview": redact(input_preview),
            },
        )

    def admin_action(self, admin_id: str, action: str, ip: str, details: Optional[Dict] = None):
        """Log admin action."""
        self.log(
            action="admin_action",
            user_id=admin_id,
            ip=ip,
            details={"admin_action": action, **(details or {})},
        )

    def _check_suspicious(self, action: str, user_id: str):
        """Check if recent actions match suspicious patterns."""
        now = time.time()
        key = f"{action}:{user_id}"

        if key not in self._action_timeline:
            self._action_timeline[key] = []

        self._action_timeline[key].append(now)

        for pattern_name, pattern in self.SUSPICIOUS_PATTERNS.items():
            if action != pattern["action"]:
                continue

            # Count actions within the window
            window_start = now - pattern["window"]
            recent = [t for t in self._action_timeline[key] if t > window_start]

            if len(recent) >= pattern["threshold"]:
                self._trigger_alert(pattern_name, pattern, user_id, recent)

    def _trigger_alert(self, pattern_name: str, pattern: dict, user_id: str, recent_events: list):
        """Trigger an alert for suspicious activity."""
        alert = {
            "type": "suspicious_activity",
            "pattern": pattern_name,
            "severity": pattern.get("severity", "medium"),
            "description": pattern.get("description", ""),
            "user_id": user_id,
            "count": len(recent_events),
            "window_seconds": pattern.get("window", 300),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Log alert
        alert_line = json.dumps(alert, ensure_ascii=False)
        logger.warning("[SECURITY ALERT] %s", alert_line)

        # Write to audit log
        if self._audit_logger:
            self._audit_logger.warning(alert_line)

        # Call registered alert handlers
        for handler in self._alert_handlers:
            try:
                handler(alert)
            except Exception as e:
                logger.error("Alert handler error: %s", str(e))

    def register_alert_handler(self, handler: Callable[[Dict], None]):
        """Register a handler function for security alerts."""
        self._alert_handlers.append(handler)

    def get_recent_events(
        self,
        limit: int = 100,
        action: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> list:
        """Get recent audit events from the log file.

        Args:
            limit: Maximum number of events to return
            action: Filter by action type
            user_id: Filter by user ID

        Returns:
            List of audit event dicts
        """
        events = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if action and event.get("action") != action:
                            continue
                        if user_id and event.get("user_id") != user_id:
                            continue
                        events.append(event)
                    except json.JSONDecodeError:
                        continue

            return events[-limit:]
        except FileNotFoundError:
            return []

    def get_stats(self) -> Dict[str, Any]:
        """Get audit log statistics."""
        events = self.get_recent_events(limit=10000)
        action_counts = {}
        result_counts = {"success": 0, "failure": 0}

        for e in events:
            action = e.get("action", "unknown")
            action_counts[action] = action_counts.get(action, 0) + 1
            result = e.get("result", "success")
            result_counts[result] = result_counts.get(result, 0) + 1

        return {
            "total_events_recent": len(events),
            "by_action": action_counts,
            "by_result": result_counts,
            "log_path": self.log_path,
        }


# Convenience decorator for audit logging
def audit_log(action: str, include_args: bool = False):
    """Decorator to automatically audit log a function call.

    Usage:
        @audit_log("data_export")
        async def export_user_data(user_id: str, request: Request):
            ...

    Args:
        action: The audit action name
        include_args: Include function arguments in audit details
    """
    def decorator(func: Callable):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Extract user_id and request from kwargs or positional args
            user_id = kwargs.get("user_id", "anonymous")
            request = kwargs.get("request", None)
            ip = ""
            user_agent = ""

            if request:
                if hasattr(request, "client") and request.client:
                    ip = request.client.host
                forwarded = request.headers.get("X-Forwarded-For", "")
                if forwarded:
                    ip = forwarded.split(",")[0].strip()
                user_agent = request.headers.get("User-Agent", "")

            details = {}
            if include_args:
                details["args"] = {
                    k: str(v)[:100] for k, v in kwargs.items()
                    if k not in ("request", "response")
                }

            try:
                result = await func(*args, **kwargs)

                # Audit log the success
                audit = AuditLogger()
                audit.log(
                    action=action,
                    user_id=user_id,
                    ip=ip,
                    user_agent=user_agent,
                    result="success",
                    details=details,
                )

                return result

            except Exception as e:
                # Audit log the failure
                audit = AuditLogger()
                audit.log(
                    action=action,
                    user_id=user_id,
                    ip=ip,
                    user_agent=user_agent,
                    result="failure",
                    details={**details, "error": str(e)},
                )
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            user_id = kwargs.get("user_id", "anonymous")
            try:
                result = func(*args, **kwargs)
                audit = AuditLogger()
                audit.log(action=action, user_id=user_id, result="success")
                return result
            except Exception as e:
                audit = AuditLogger()
                audit.log(action=action, user_id=user_id, result="failure", details={"error": str(e)})
                raise

        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
