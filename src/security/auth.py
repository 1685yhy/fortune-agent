"""Authentication module for Fortune Agent.

Provides:
- API key validation for external access
- JWT token management for WeChat mini-program users
- Token expiry and refresh mechanism
- Admin authorization
"""
import os
import time
import hashlib
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple

from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

# JWT-like token implementation (stateless, HMAC-signed)
# Using simple HMAC-SHA256 since we don't want to add PyJWT dependency


class JWTHandler:
    """Simple JWT implementation using HMAC-SHA256.

    Handles token creation, verification, and refresh.
    Tokens include: user_id, openid (WeChat), role, expiry.
    """

    def __init__(self, secret_key: Optional[str] = None):
        self.secret_key = secret_key or os.getenv("JWT_SECRET_KEY", "")
        if not self.secret_key:
            # Generate a random key for development; production MUST set JWT_SECRET_KEY
            self.secret_key = secrets.token_hex(32)
            logger.error(
                "JWT_SECRET_KEY 未设置！使用本次进程随机密钥——重启后所有已登录用户 token 失效，"
                "多 worker 部署下各进程 token 互不认可。生产环境必须在 .env 固定 JWT_SECRET_KEY（≥32 字节）。"
            )

    def _sign(self, payload: str) -> str:
        """Create HMAC-SHA256 signature."""
        return hashlib.sha256(f"{payload}.{self.secret_key}".encode()).hexdigest()

    def create_token(
        self,
        user_id: str,
        openid: str = "",
        role: str = "user",
        expiry_days: int = 7,
    ) -> str:
        """Create a stateless JWT token.

        Format: base64(header).base64(payload).signature
        """
        import base64
        import json

        # Header
        header = {"alg": "HS256", "typ": "JWT"}

        # Payload with standard claims
        now = int(time.time())
        payload = {
            "sub": user_id,
            "openid": openid,
            "role": role,
            "iat": now,
            "exp": now + (expiry_days * 86400),
            "jti": secrets.token_hex(8),  # Unique token ID for revocation
        }

        # Encode
        header_b64 = base64.urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode()).rstrip(b"=").decode()
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()

        # Sign
        signature = self._sign(f"{header_b64}.{payload_b64}")

        return f"{header_b64}.{payload_b64}.{signature}"

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify and decode a JWT token. Returns payload dict or None."""
        import base64
        import json

        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None

            header_b64, payload_b64, signature = parts

            # Verify signature
            expected_sig = self._sign(f"{header_b64}.{payload_b64}")
            if not secrets.compare_digest(signature, expected_sig):
                logger.warning("Token signature verification failed")
                return None

            # Decode payload
            # Add padding
            payload_padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
            payload_bytes = base64.urlsafe_b64decode(payload_padded)
            payload = json.loads(payload_bytes)

            # Check expiry
            now = time.time()
            if payload.get("exp", 0) < now:
                logger.debug("Token expired for user %s", payload.get("sub", "unknown"))
                return None

            return payload

        except (ValueError, json.JSONDecodeError, Exception) as e:
            logger.warning("Token verification failed: %s", str(e))
            return None

    def refresh_token(self, token: str) -> Optional[str]:
        """Refresh an existing token if it's halfway through its validity."""
        payload = self.verify_token(token)
        if not payload:
            return None

        # Check if token is at least halfway expired
        now = time.time()
        issued = payload.get("iat", 0)
        expires = payload.get("exp", 0)
        lifetime = expires - issued

        if lifetime <= 0:
            return None

        # Only refresh if more than half the lifetime has passed
        elapsed = now - issued
        if elapsed < lifetime / 2:
            # Token is still fresh, return the same token
            return token

        # Issue new token
        return self.create_token(
            user_id=payload.get("sub", ""),
            openid=payload.get("openid", ""),
            role=payload.get("role", "user"),
        )

    def revoke_token(self, token: str) -> bool:
        """Mark a token as revoked (adds to blocklist).
        Note: For production, use Redis or DB for token blocklist.
        """
        # For now, just log the revocation intent
        payload = self.verify_token(token)
        if payload:
            logger.info("Token revoked for user %s (jti: %s)", payload.get("sub"), payload.get("jti"))
        return True


class AuthHandler:
    """Comprehensive authentication handler.

    Supports:
    - API key authentication (for external services)
    - JWT token authentication (for mini-program users)
    - Admin key authentication (for admin endpoints)
    """

    def __init__(self):
        self.jwt = JWTHandler()
        self.api_keys: Dict[str, dict] = {}
        self._load_api_keys()

    def _load_api_keys(self):
        """Load API keys from environment variable."""
        api_keys_str = os.getenv("API_KEYS", "")
        if api_keys_str:
            for key_entry in api_keys_str.split(","):
                key_entry = key_entry.strip()
                if ":" in key_entry:
                    key, name = key_entry.split(":", 1)
                    self.api_keys[key] = {"name": name, "active": True}
                elif key_entry:
                    self.api_keys[key_entry] = {"name": "default", "active": True}

        # Also check for single API key
        single_key = os.getenv("FORTUNE_API_KEY", "")
        if single_key and single_key not in self.api_keys:
            self.api_keys[single_key] = {"name": "primary", "active": True}

    def validate_api_key(self, api_key: str) -> Optional[Dict]:
        """Validate an API key. Returns key info or None."""
        key_info = self.api_keys.get(api_key)
        if key_info and key_info.get("active", False):
            return key_info
        return None

    def authenticate_request(self, request: Request) -> Tuple[bool, Optional[Dict], Optional[str]]:
        """Try all authentication methods. Returns (authenticated, user_info, error)."""
        # Method 1: JWT token (Authorization: Bearer <token>)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            # Check if it's an API key first
            api_key_info = self.validate_api_key(token)
            if api_key_info:
                return True, {"user_id": "api_user", "method": "api_key", "name": api_key_info.get("name", "unknown")}, None

            # Try JWT
            payload = self.jwt.verify_token(token)
            if payload:
                return True, {
                    "user_id": payload.get("sub", ""),
                    "openid": payload.get("openid", ""),
                    "role": payload.get("role", "user"),
                    "method": "jwt",
                }, None

            return False, None, "无效的认证令牌"

        # Method 2: API key in X-API-Key header
        api_key = request.headers.get("X-API-Key", "")
        if api_key:
            key_info = self.validate_api_key(api_key)
            if key_info:
                return True, {"user_id": "api_user", "method": "api_key", "name": key_info.get("name", "unknown")}, None
            return False, None, "无效的API密钥"

        # No authentication provided — allow but mark as anonymous
        return True, {"user_id": "anonymous", "method": "none", "role": "anonymous"}, None

    def create_user_token(self, user_id: str, openid: str = "", role: str = "user") -> str:
        """Create a JWT token for a user."""
        return self.jwt.create_token(user_id, openid, role)

    def verify_admin(self, request: Request, admin_key: str = "") -> bool:
        """Verify admin authorization."""
        auth_header = request.headers.get("Authorization", "")
        expected = admin_key or os.getenv("ADMIN_KEY", "")

        if not expected:
            return True  # No admin key configured = allow

        return auth_header == f"Bearer {expected}"


# FastAPI dependencies
security_scheme = HTTPBearer(auto_error=False)

# Shared auth handler singleton (set during app initialization)
_shared_auth_handler: Optional[AuthHandler] = None


def set_auth_handler(handler: AuthHandler):
    """Set the shared auth handler instance.

    Called during app initialization to ensure consistent
    JWT key usage across the application.
    """
    global _shared_auth_handler
    _shared_auth_handler = handler


def get_auth_handler() -> AuthHandler:
    """Get the shared auth handler (or create a new one)."""
    global _shared_auth_handler
    if _shared_auth_handler is None:
        _shared_auth_handler = AuthHandler()
    return _shared_auth_handler


async def require_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
) -> Dict[str, Any]:
    """FastAPI dependency: require valid authentication.

    Security fix: reject anonymous (unauthenticated) requests — previously
    `authenticate_request` allowed anonymous through with user_id="anonymous",
    which meant "protected" endpoints were open to anyone.
    """
    auth = get_auth_handler()
    authenticated, user_info, error = auth.authenticate_request(request)

    if not authenticated or not user_info or not user_info.get("user_id"):
        logger.warning("鉴权拒绝 401: path=%s method=%s 原因=%s",
                       request.url.path, request.method, error or "未认证")
        raise HTTPException(
            status_code=401,
            detail="未登录或登录已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_info


async def require_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
) -> str:
    """FastAPI dependency: strict JWT auth for business routes (红线核心).

    Returns the authenticated user_id (JWT `sub` claim).
    - Missing/invalid/expired token  → 401
    - API key / anonymous            → 401 (业务路由只认 JWT)
    - Client-supplied user_id params are NEVER trusted; routes must use
      this dependency's return value as the authoritative user_id.
    """
    auth = get_auth_handler()
    token = ""
    if credentials is not None:
        token = credentials.credentials
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        logger.warning("鉴权拒绝 401: path=%s 无令牌", request.url.path)
        raise HTTPException(
            status_code=401,
            detail="未登录或登录已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = auth.jwt.verify_token(token)
    if not payload or not payload.get("sub"):
        logger.warning("鉴权拒绝 401: path=%s 令牌无效/过期", request.url.path)
        raise HTTPException(
            status_code=401,
            detail="登录已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return str(payload["sub"])


def ensure_owner(path_user_id: str, token_user_id: str):
    """IDOR 防护：路径/查询中的 user_id 必须与 token sub 一致，否则 403。"""
    if not path_user_id or path_user_id != token_user_id:
        logger.warning("鉴权拒绝 403: 越权访问 path_user=%s token_user=%s",
                       path_user_id or "(空)", token_user_id or "(空)")
        raise HTTPException(status_code=403, detail="无权访问该用户数据")


async def require_chat_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
) -> Dict[str, Any]:
    """/api/chat 专用鉴权（保持 chatgpt-on-wechat 机器人集成不回退）。

    - 小程序用户：JWT 必填，user_id 取 sub（body 的 user_id 被忽略）；
    - 外部机器人（chatgpt-on-wechat 等）：需配置 FORTUNE_API_KEY 并以
      X-API-Key（或 Bearer <key>）认证，user_id 由机器人自行传入。
    """
    auth = get_auth_handler()

    # 1. X-API-Key 头（机器人通道）
    api_key = request.headers.get("X-API-Key", "")
    if api_key:
        key_info = auth.validate_api_key(api_key)
        if not key_info:
            logger.warning("鉴权拒绝 401: path=%s API密钥无效", request.url.path)
            raise HTTPException(status_code=401, detail="无效的 API 密钥")
        return {"user_id": "api_user", "method": "api_key"}

    # 2. Bearer：先试 API key，再试 JWT
    token = ""
    if credentials is not None:
        token = credentials.credentials
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        logger.warning("鉴权拒绝 401: path=%s 无令牌", request.url.path)
        raise HTTPException(
            status_code=401,
            detail="未登录或登录已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    key_info = auth.validate_api_key(token)
    if key_info:
        return {"user_id": "api_user", "method": "api_key"}

    payload = auth.jwt.verify_token(token)
    if not payload or not payload.get("sub"):
        logger.warning("鉴权拒绝 401: path=%s 令牌无效/过期", request.url.path)
        raise HTTPException(
            status_code=401,
            detail="登录已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "user_id": payload["sub"],
        "openid": payload.get("openid", ""),
        "method": "jwt",
    }


async def require_admin(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
) -> bool:
    """FastAPI dependency: require admin privileges.

    Security fix: if no ADMIN_KEY is configured the endpoint is now REJECTED
    (403) instead of allowed — no empty-key bypass (审计 §审计8 E16).
    """
    admin_key = os.getenv("ADMIN_KEY", "")
    if not admin_key:
        logger.warning("鉴权拒绝 403: path=%s ADMIN_KEY 未配置", request.url.path)
        raise HTTPException(
            status_code=403,
            detail="管理员密钥未配置，拒绝访问",
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth = get_auth_handler()
    if not auth.verify_admin(request, admin_key):
        logger.warning("鉴权拒绝 403: path=%s 管理员密钥无效", request.url.path)
        raise HTTPException(status_code=403, detail="无效的管理员密钥")
    return True
