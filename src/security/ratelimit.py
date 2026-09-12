"""Rate limiting middleware for FastAPI.

Provides multi-layer rate limiting:
- Per-IP: 30 requests/minute for chat, 10/minute for analysis
- Per-user: 100 requests/hour
- Burst allowance: +50% for 5 seconds
"""
import time
import logging
from collections import defaultdict
from typing import Dict, Tuple, Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class TokenBucket:
    """Token bucket algorithm for burst-aware rate limiting."""

    def __init__(self, capacity: int, refill_rate: float, refill_period: float = 1.0):
        self.capacity = capacity
        self.tokens = float(capacity)
        self.refill_rate = refill_rate
        self.refill_period = refill_period
        self.last_refill = time.monotonic()

    def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens. Returns True if allowed."""
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    @property
    def available_tokens(self) -> float:
        self._refill()
        return self.tokens


class SlidingWindowCounter:
    """Sliding window rate counter."""

    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: list = []  # list of timestamps

    def _prune(self):
        now = time.monotonic()
        cutoff = now - self.window_seconds
        self.requests = [t for t in self.requests if t > cutoff]

    def allow(self) -> Tuple[bool, int]:
        """Check if request is allowed. Returns (allowed, retry_after_seconds)."""
        self._prune()
        now = time.monotonic()
        if len(self.requests) < self.max_requests:
            self.requests.append(now)
            return True, 0
        # Calculate retry-after
        oldest = self.requests[0]
        retry_after = int((oldest + self.window_seconds - now))
        return False, max(1, retry_after)


class RateLimiter:
    """Multi-strategy rate limiter.

    Supports IP-based, user-based, and endpoint-specific rate limits.
    """

    def __init__(self):
        # Per-IP limits: (max_requests, window_seconds)
        self._ip_limits: Dict[str, Dict[str, SlidingWindowCounter]] = defaultdict(dict)
        # Per-user limits
        self._user_limits: Dict[str, SlidingWindowCounter] = {}
        # Burst buckets: IP -> {route -> TokenBucket}
        self._burst_buckets: Dict[str, Dict[str, TokenBucket]] = defaultdict(dict)

        # Default limits
        self.ip_chat_limit = (30, 60)          # 30 req/min for chat
        self.ip_analysis_limit = (10, 60)       # 10 req/min for analysis
        self.user_hourly_limit = (100, 3600)    # 100 req/hour per user
        self.burst_multiplier = 1.5             # +50% burst allowance
        self.burst_window = 5                   # 5 seconds burst window

    def _get_route_group(self, path: str) -> str:
        """Categorize endpoint into rate limit group."""
        # k39 审查 I1：/v1/*（OpenAI 兼容外部通道）＝ LLM 成本面，与 /api/chat
        # 同档（同一档参数、同一计数器组语义）。改前 /v1/* 根本不进限流
        # （见 RATE_LIMITED_PATHS），通道复活后是唯一无上限的 LLM 面。
        if path == "/v1" or path.startswith("/v1/"):
            return "chat"
        if "/chat" in path:
            return "chat"
        if any(x in path for x in ("/analysis", "/face-reading", "/palm-reading", "/calendar", "/compatibility")):
            return "analysis"
        return "default"

    def ip_limit_for(self, path: str) -> Tuple[int, int]:
        """该路径的 (max_requests, window_seconds)——单一事实源。

        429 响应的 `X-RateLimit-Limit` 与计数窗口都取自这里，避免三处各写一遍
        常量（改前 429 头对 default 档硬写 "10"、实际是 60，就是分头写导致的）。
        """
        group = self._get_route_group(path)
        if group == "chat":
            return self.ip_chat_limit
        if group == "analysis":
            return self.ip_analysis_limit
        return (60, 60)  # 60 req/min for default

    def check_ip(self, ip: str, path: str) -> Tuple[bool, int]:
        """Check IP-based rate limit. Returns (allowed, retry_after)."""
        group = self._get_route_group(path)
        max_req, window = self.ip_limit_for(path)

        if ip not in self._ip_limits:
            self._ip_limits[ip] = {}

        if group not in self._ip_limits[ip]:
            self._ip_limits[ip][group] = SlidingWindowCounter(max_req, window)

        allowed, retry_after = self._ip_limits[ip][group].allow()

        # Apply burst allowance if denied at normal rate
        if not allowed:
            burst_allowed = self._check_burst(ip, path)
            if burst_allowed:
                return True, 0

        return allowed, retry_after

    def _check_burst(self, ip: str, path: str) -> bool:
        """Check burst bucket for temporary overage allowance."""
        group = self._get_route_group(path)
        base_capacity = self.ip_limit_for(path)[0]

        burst_capacity = int(base_capacity * self.burst_multiplier)

        if ip not in self._burst_buckets:
            self._burst_buckets[ip] = {}

        if group not in self._burst_buckets[ip]:
            # Refill the burst bucket over the burst window
            refill_rate = burst_capacity / max(self.burst_window, 1)
            self._burst_buckets[ip][group] = TokenBucket(burst_capacity, refill_rate)

        return self._burst_buckets[ip][group].consume()

    def check_user(self, user_id: str) -> Tuple[bool, int]:
        """Check user-based rate limit."""
        if user_id not in self._user_limits:
            self._user_limits[user_id] = SlidingWindowCounter(
                self.user_hourly_limit[0], self.user_hourly_limit[1]
            )
        return self._user_limits[user_id].allow()

    def cleanup(self, max_age: float = 3600):
        """Periodically clean up stale rate limit data."""
        now = time.monotonic()
        # Implementation can be extended for periodic cleanup
        pass


RATE_LIMITED_PATHS = [
    # k39 审查 I1：/v1/*（OpenAI 兼容外部通道）此前**完全无限流**，通道从 422
    # 复活后是唯一无上限的 LLM 成本面，纳入既有中间件（不新造限流设施）。
    "/v1",
    "/api/chat",
    "/api/analysis",
    "/api/face-reading",
    "/api/palm-reading",
    "/api/calendar",
    "/api/compatibility",
    "/api/user",
    "/api/feedback",
]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for rate limiting."""

    def __init__(self, app: ASGIApp, limiter: Optional[RateLimiter] = None):
        super().__init__(app)
        self.limiter = limiter or RateLimiter()
        self._paths_to_limit = RATE_LIMITED_PATHS

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip rate limiting for non-API paths and health checks
        if not any(path.startswith(p) for p in self._paths_to_limit):
            return await call_next(request)

        # Get client IP
        forwarded = request.headers.get("X-Forwarded-For", "")
        ip = forwarded.split(",")[0].strip() if forwarded else request.client.host if request.client else "unknown"
        if ip == "unknown" or not ip:
            ip = "127.0.0.1"

        # Check IP-based limit
        ip_allowed, ip_retry_after = self.limiter.check_ip(ip, path)
        if not ip_allowed:
            logger.warning("Rate limit exceeded for IP %s on %s", ip, path)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "请求过于频繁，请稍后再试。",
                    "code": "rate_limit_exceeded",
                    "retry_after": ip_retry_after,
                },
                headers={
                    "Retry-After": str(ip_retry_after),
                    # 实际档位（单一事实源）；改前 default 档硬写 "10" 而实际 60
                    "X-RateLimit-Limit": str(self.limiter.ip_limit_for(path)[0]),
                    "X-RateLimit-Remaining": "0",
                },
            )

        # Check user-based limit (if user_id in request)
        user_id = self._extract_user_id(request)
        if user_id:
            user_allowed, user_retry_after = self.limiter.check_user(user_id)
            if not user_allowed:
                logger.warning("Rate limit exceeded for user %s on %s", user_id, path)
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "您的请求频率已达上限，请稍后再试。",
                        "code": "user_rate_limit_exceeded",
                        "retry_after": user_retry_after,
                    },
                    headers={"Retry-After": str(user_retry_after)},
                )

        response = await call_next(request)
        return response

    def _extract_user_id(self, request: Request) -> Optional[str]:
        """Extract user_id from request body or query params."""
        # k39 审查 I1：/v1/* 的身份由 API key 决定（与路由同源判据）——请求里的
        # user_id（查询参数等）**不得**作为计数身份，否则刷量者可以把配额计到
        # 别人头上（或换参数绕过 per-user 上限）。未绑定 → 无身份（只吃 IP 档）。
        if request.url.path == "/v1" or request.url.path.startswith("/v1/"):
            return self._identity_from_api_key(request) or None

        # Try query params
        user_id = request.query_params.get("user_id")
        if user_id:
            return user_id

        # Try path params (for /api/user/{user_id} style)
        path_parts = request.url.path.split("/")
        for i, part in enumerate(path_parts):
            if part == "user" and i + 1 < len(path_parts):
                candidate = path_parts[i + 1]
                # Avoid matching parameter names or endpoints
                if candidate not in ("export", "data", "history", "accuracy") and not candidate.startswith("{"):
                    return candidate

        return None

    @staticmethod
    def _identity_from_api_key(request: Request) -> str:
        """请求携带的 API key 所绑定的身份（无 key / 未绑定 / 异常 → 空串）。"""
        token = request.headers.get("X-API-Key", "")
        if not token:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()
        if not token:
            return ""
        try:
            from src.security.auth import get_auth_handler
            return get_auth_handler().bound_user_for_key(token) or ""
        except Exception:  # noqa: BLE001 — 计数维度取不到身份不影响放行（IP 档仍在）
            return ""
