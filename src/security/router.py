"""Security-related API routes for Fortune Agent.

Provides:
- JWT token management (login, refresh)
- Privacy endpoints (data export, deletion)
- Security status/info endpoints
- Disclaimer endpoint
"""
import os
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Request, Depends, Header, Query, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .auth import AuthHandler, require_auth, require_admin
from .privacy import PrivacyManager, PIPL_DISCLAIMER
# k78：用户可见文案（数据删除响应）的单一事实源——与 src/main.py 同源
from .account_copy import DATA_PURGE_INCOMPLETE_NOTICE, USER_DATA_PURGED_NOTICE
from .audit import AuditLogger, audit_log
from .sanitizer import InputSanitizer
from .encryption import DataEncryptor
from .ratelimit import RateLimiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/security", tags=["security"])

# Global instances (set during app initialization)
_auth_handler: Optional[AuthHandler] = None
_privacy_manager: Optional[PrivacyManager] = None
_audit_logger: Optional[AuditLogger] = None
_sanitizer: Optional[InputSanitizer] = None
_encryptor: Optional[DataEncryptor] = None


def init_security_router(
    db_path: str = "",
    auth_handler: Optional[AuthHandler] = None,
    privacy_manager: Optional[PrivacyManager] = None,
    audit_logger: Optional[AuditLogger] = None,
    sanitizer: Optional[InputSanitizer] = None,
    encryptor: Optional[DataEncryptor] = None,
):
    """Initialize security router with application instances."""
    global _auth_handler, _privacy_manager, _audit_logger, _sanitizer, _encryptor

    _auth_handler = auth_handler or AuthHandler()
    _encryptor = encryptor or DataEncryptor()
    _audit_logger = audit_logger or AuditLogger()
    _sanitizer = sanitizer or InputSanitizer()

    if db_path:
        _privacy_manager = privacy_manager or PrivacyManager(db_path, _encryptor)


# ── Request/Response Models ──────────────────────────────────

class TokenRequest(BaseModel):
    code: str = ""


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 604800  # 7 days in seconds
    user_id: str = ""


class LoginRequest(BaseModel):
    user_id: str
    password: Optional[str] = None  # Not used with WeChat OAuth


class RefreshRequest(BaseModel):
    refresh_token: str


class DataExportResponse(BaseModel):
    status: str
    export_time: str
    disclaimer: str = PIPL_DISCLAIMER
    data: Optional[Dict[str, Any]] = None


class DataDeleteResponse(BaseModel):
    status: str
    message: str
    # k78-必修2 附带修（同端点，实测）：k76 起 `delete_user_data()` 的返回值里
    # 多了 `files: {类别: 个数}` 与 `retained: {表: 法定依据}` 两个**非 int** 的子字典，
    # 而这里写死 `Dict[str, int]` → 响应模型校验必然失败 → 端点**永远** 500
    # 「删除失败，请稍后重试」（用户根本看不到 message）。改成 Any 后与 main.py 的
    # 同一响应结构（直接回 dict）口径一致。（该缺陷是 k76 引入的既有回归，非本批产生）
    records_deleted: Optional[Dict[str, Any]] = None
    disclaimer: str = PIPL_DISCLAIMER


class SecurityInfo(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    features: Dict[str, bool] = {}
    disclaimer: str = PIPL_DISCLAIMER


class DisclaimerResponse(BaseModel):
    disclaimer: str = PIPL_DISCLAIMER
    compliance: str = "《中华人民共和国个人信息保护法》"


# ── Endpoints ────────────────────────────────────────────────

@router.get("/disclaimer", response_model=DisclaimerResponse)
async def get_disclaimer():
    """Get the platform disclaimer and compliance information."""
    return DisclaimerResponse()


@router.get("/info", response_model=SecurityInfo)
async def get_security_info():
    """Get security system information (public)."""
    return SecurityInfo(
        features={
            "rate_limiting": True,
            "input_sanitization": True,
            "encryption_at_rest": bool(os.getenv("ENCRYPTION_KEY", "")),
            "audit_logging": True,
            "data_retention": True,
            "data_export": True,
            "data_deletion": True,
        }
    )


@router.post("/token", response_model=TokenResponse)
async def create_token(req: LoginRequest):
    """Create a JWT token for a user (mini-program login simulation).

    Security fix: this endpoint could mint a valid token for ANY user_id
    (token forging). It is now gated behind DEV_TOKEN_ENDPOINT=1 (default
    off); production login must go through POST /api/user/login which
    verifies the WeChat code.
    """
    if _auth_handler is None:
        raise HTTPException(status_code=503, detail="Security system not ready")

    if os.getenv("DEV_TOKEN_ENDPOINT", "0") != "1":
        raise HTTPException(status_code=403, detail="模拟登录端点已禁用，请使用 /api/user/login")

    # In production, this would verify WeChat code via:
    # GET https://api.weixin.qq.com/sns/jscode2session?appid=APPID&secret=SECRET&js_code=CODE&grant_type=authorization_code

    user_id = _sanitizer.sanitize(req.user_id) if _sanitizer else req.user_id
    token = _auth_handler.create_user_token(user_id=user_id, openid=user_id)

    if _audit_logger:
        _audit_logger.log(
            action="login",
            user_id=user_id,
            result="success",
            details={"method": "jwt_create"},
        )

    return TokenResponse(
        access_token=token,
        user_id=user_id,
    )


@router.post("/token/refresh", response_model=TokenResponse)
async def refresh_token(req: RefreshRequest):
    """Refresh an existing JWT token."""
    if _auth_handler is None:
        raise HTTPException(status_code=503, detail="Security system not ready")

    new_token = _auth_handler.jwt.refresh_token(req.refresh_token)
    if not new_token:
        raise HTTPException(status_code=401, detail="Token刷新失败，请重新登录")

    # Decode to get user_id
    payload = _auth_handler.jwt.verify_token(new_token)
    user_id = payload.get("sub", "") if payload else ""

    if _audit_logger:
        _audit_logger.log(action="token_refresh", user_id=user_id, result="success")

    return TokenResponse(access_token=new_token, user_id=user_id)


@router.post("/token/revoke")
async def revoke_token(authorization: str = Header("")):
    """Revoke the current JWT token."""
    if _auth_handler is None:
        raise HTTPException(status_code=503, detail="Security system not ready")

    token = ""
    if authorization.startswith("Bearer "):
        token = authorization[7:]

    if token:
        _auth_handler.jwt.revoke_token(token)
        if _audit_logger:
            _audit_logger.log(action="token_revoke", result="success")

    return {"status": "ok", "message": "令牌已撤销"}


# ── Privacy Endpoints ────────────────────────────────────────

@router.get("/user/{user_id}/export")
async def export_user_data(
    user_id: str,
    request: Request,
    auth_info: dict = Depends(require_auth),
):
    """Export all data for a user (data portability).

    PIPL Art. 45: Individuals have the right to transfer their data.

    Security fix: owner check — token sub must match the requested user_id.
    """
    if _privacy_manager is None:
        raise HTTPException(status_code=503, detail="Privacy system not ready")

    from .auth import ensure_owner
    ensure_owner(user_id, auth_info.get("user_id", ""))

    # Audit log
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
    user_agent = request.headers.get("User-Agent", "")
    _audit_logger.data_export(user_id, ip, user_agent)

    try:
        data = _privacy_manager.export_user_data(user_id)
        if data is None:
            raise HTTPException(status_code=404, detail="用户不存在或无数据")

        return DataExportResponse(
            status="ok",
            export_time=datetime.now(timezone.utc).isoformat(),
            data=data,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Data export failed for user %s: %s", user_id, str(e))
        raise HTTPException(status_code=500, detail="导出失败，请稍后重试")


@router.delete("/user/{user_id}/data")
async def delete_user_data(
    user_id: str,
    request: Request,
    # k78：本条 description 出现在对外可读的 OpenAPI 文档里，属用户/接入方可见面。
    # 与响应 message 同口径：不再写"不可恢复"（与"备份仍覆盖时可人工尝试找回"冲突），
    # 改用本仓统一词汇"不可自助恢复"。
    confirm: bool = Query(True, description="确认删除（删除后不可自助恢复）"),
    auth_info: dict = Depends(require_auth),
):
    """Delete all data for a user (Right to be Forgotten).

    PIPL Art. 47: Individuals have the right to request deletion.

    Security fix: owner check — token sub must match the requested user_id.
    """
    if _privacy_manager is None:
        raise HTTPException(status_code=503, detail="Privacy system not ready")

    from .auth import ensure_owner
    ensure_owner(user_id, auth_info.get("user_id", ""))

    if not confirm:
        raise HTTPException(status_code=400, detail="请确认删除操作（confirm=true）")

    # Audit log
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
    _audit_logger.data_deletion(user_id, ip)

    try:
        deleted = _privacy_manager.delete_user_data(user_id)

        # k79-M1：同 `src/main.py` 的同一端点 —— **真删失败**时不得再回"删除完成"
        # （改前两张表删失败也只写日志，端点照样 ok；与"本来就没有"不可区分）。
        # 表不存在（`no such table`，环境差异）不算失败，仍 200。
        if not deleted.get("ok", True):
            logger.error("Data deletion INCOMPLETE for user %s: %s",
                         user_id, deleted.get("failed_tables"))
            return JSONResponse(status_code=500, content={
                "status": "incomplete",
                "message": DATA_PURGE_INCOMPLETE_NOTICE,
                "records_deleted": deleted,
                "failed_tables": deleted.get("failed_tables") or {},
                "failed_files": deleted.get("failed_files") or [],
                "disclaimer": PIPL_DISCLAIMER,
            })
        logger.warning("User %s requested data deletion", user_id)

        return DataDeleteResponse(
            status="ok",
            # k78：与 src/main.py 的同一端点同一常量（改前两处各写一份同一句话）
            message=USER_DATA_PURGED_NOTICE,
            records_deleted=deleted,
        )

    except Exception as e:
        logger.error("Data deletion failed for user %s: %s", user_id, str(e))
        raise HTTPException(status_code=500, detail="删除失败，请稍后重试")


@router.post("/user/{user_id}/anonymize")
async def anonymize_user_data(
    user_id: str,
    request: Request,
    auth_info: dict = Depends(require_auth),
):
    """Anonymize user data (remove PII, keep aggregated stats).

    Security fix: owner check — token sub must match the requested user_id.
    """
    if _privacy_manager is None:
        raise HTTPException(status_code=503, detail="Privacy system not ready")

    from .auth import ensure_owner
    ensure_owner(user_id, auth_info.get("user_id", ""))

    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
    _audit_logger.log(action="data_anonymization", user_id=user_id, ip=ip)

    try:
        success = _privacy_manager.anonymize_user_data(user_id)
        if not success:
            raise HTTPException(status_code=500, detail="匿名化处理失败")

        return {
            "status": "ok",
            "message": "个人数据已匿名化处理（PII已移除）",
            "disclaimer": PIPL_DISCLAIMER,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Anonymization failed: %s", str(e))
        raise HTTPException(status_code=500, detail="处理失败")


@router.get("/retention/info")
async def get_retention_info():
    """Get data retention policy information."""
    if _privacy_manager is None:
        raise HTTPException(status_code=503, detail="Privacy system not ready")

    return _privacy_manager.get_data_retention_info()


@router.post("/retention/cleanup")
async def run_retention_cleanup(
    dry_run: bool = Query(True, description="仅查看，不实际操作"),
    auth_info: dict = Depends(require_admin),
):
    """Run data retention cleanup (admin only)."""
    if _privacy_manager is None:
        raise HTTPException(status_code=503, detail="Privacy system not ready")

    stats = _privacy_manager.cleanup_inactive_users(dry_run=dry_run)

    if _audit_logger:
        _audit_logger.log(
            action="data_retention_cleanup",
            result="dry_run" if dry_run else "completed",
            details={"total_inactive": stats["total_inactive"]},
        )

    return stats


# ── Admin Security Endpoints ──────────────────────────────────

@router.get("/audit/logs")
async def get_audit_logs(
    limit: int = Query(100, ge=1, le=1000),
    action: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    auth_info: dict = Depends(require_admin),
):
    """Get recent audit logs (admin only)."""
    if _audit_logger is None:
        raise HTTPException(status_code=503, detail="Audit system not ready")

    return {
        "logs": _audit_logger.get_recent_events(limit=limit, action=action, user_id=user_id),
        "total_returned": limit,
    }


@router.get("/audit/stats")
async def get_audit_stats(auth_info: dict = Depends(require_admin)):
    """Get audit log statistics (admin only)."""
    if _audit_logger is None:
        raise HTTPException(status_code=503, detail="Audit system not ready")

    return _audit_logger.get_stats()


@router.get("/sanitizer/stats")
async def get_sanitizer_stats(auth_info: dict = Depends(require_admin)):
    """Get sanitizer statistics (admin only)."""
    if _sanitizer is None:
        raise HTTPException(status_code=503, detail="Sanitizer not ready")

    return _sanitizer.get_stats()


@router.get("/status")
async def get_security_status(auth_info: dict = Depends(require_admin)):
    """Get full security system status (admin only)."""
    return {
        "status": "operational",
        "modules": {
            "rate_limiter": {
                "enabled": True,
                "ip_chat_limit": "30r/m",
                "ip_analysis_limit": "10r/m",
                "user_hourly_limit": "100r/h",
                "burst_enabled": True,
            },
            "auth": {
                "enabled": True,
                "jwt_expiry_days": 7,
                "api_keys_configured": bool(os.getenv("FORTUNE_API_KEY", "") or os.getenv("API_KEYS", "")),
                "admin_key_configured": bool(os.getenv("ADMIN_KEY", "")),
            },
            "sanitizer": {
                "enabled": True,
                "max_input_length": InputSanitizer.MAX_INPUT_LENGTH,
                "patterns_tracked": len(InputSanitizer.__dict__.get("_blocked_types", [])) + 5,
            },
            "encryption": {
                "enabled": bool(os.getenv("ENCRYPTION_KEY", "")),
                "algorithm": "AES-256-GCM",
                "key_rotation": True,
            },
            "privacy": {
                "enabled": True,
                "retention_days": 180,
            },
            "audit": {
                "enabled": True,
                "log_path": os.getenv("AUDIT_LOG_PATH", "/opt/fortune-agent/logs/audit.log"),
                "alert_patterns": list(AuditLogger.SUSPICIOUS_PATTERNS.keys()),
            },
        },
    }
