"""Response cache with TTL — reduces latency for high-frequency requests.

D2 speed optimization. Caches common queries and chart images to cut
response time from 3-10s to <1s for 30%+ of requests.

Step 4: Added CacheEntry with per-entry TTL, key-based get/set, and
type-specific TTL constants for daily/hourly/xuetang endpoints.
"""
import hashlib
import time
import threading
from typing import Dict, Optional, Tuple, Any
from collections import OrderedDict


# ── TTL Constants ────────────────────────────────────────────────────────────

TTL_DAILY_FORTUNE = 3600       # 1 hour
TTL_HOURLY_FORTUNE = 600       # 10 minutes
TTL_XUETANG_TOPICS = 86400     # 24 hours
TTL_XUETANG_LESSON = 3600      # 1 hour
TTL_DEFAULT = 300              # 5 minutes
TTL_PRECOMPUTE_DAILY = 3600    # 1 hour (for precomputed anonymous daily content)


# ── CacheEntry ───────────────────────────────────────────────────────────────

class CacheEntry:
    """A cache entry with TTL tracking.

    Stores the value along with creation timestamp and TTL duration.
    The is_expired property checks whether the entry has outlived its TTL.
    """

    def __init__(self, value: Any, ttl_seconds: int = TTL_DEFAULT):
        self.value = value
        self.created_at = time.time()
        self.ttl = ttl_seconds

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl

    @property
    def age(self) -> float:
        """Return current age in seconds."""
        return time.time() - self.created_at

    @property
    def remaining_ttl(self) -> float:
        """Return remaining TTL in seconds (may be negative if expired)."""
        return self.ttl - self.age


# ── ResponseCache ────────────────────────────────────────────────────────────

class ResponseCache:
    """Thread-safe in-memory cache with TTL and LRU eviction.

    Uses OrderedDict for LRU behavior — oldest entries evicted when full.
    Supports both key-based get/set (Step 4 API) and legacy message-based get/set.
    """

    def __init__(self, max_size: int = 500, default_ttl: int = 3600):
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self.max_size = max_size
        self.default_ttl = default_ttl  # 1 hour default

    def _key(self, message: str, user_id: str = "") -> str:
        """Generate a cache key from message content (legacy method)."""
        normalized = message.strip().lower()
        return hashlib.md5(normalized.encode()).hexdigest()

    # ── New key-based API (Step 4) ───────────────────────────────────────

    def set(self, key: str, value: Any, user_id: str = "",
            ttl_seconds: int = TTL_DEFAULT) -> None:
        """Cache a value with per-entry TTL.

        Args:
            key: Cache key (typically a descriptive string or URL path).
            value: Any serializable value to cache.
            user_id: Optional user identifier for scoping.
            ttl_seconds: Time-to-live in seconds (default 300 / 5 min).
        """
        full_key = self._make_key(key, user_id)
        entry = CacheEntry(value, ttl_seconds)
        with self._lock:
            # Evict oldest if at capacity
            while len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            self._cache[full_key] = entry

    def get(self, key: str, user_id: str = "") -> Optional[Any]:
        """Get cached value. Returns None if miss or expired.

        Args:
            key: Cache key used during set().
            user_id: Optional user identifier for scoping.

        Returns:
            Cached value if found and not expired, else None.
        """
        full_key = self._make_key(key, user_id)
        with self._lock:
            entry = self._cache.get(full_key)
            if entry is not None:
                if entry.is_expired:
                    del self._cache[full_key]
                    return None
                # Move to end (most recently used)
                self._cache.move_to_end(full_key)
                return entry.value
        return None

    def _make_key(self, key: str, user_id: str = "") -> str:
        """Build a full cache key from logical key + user scope."""
        raw = f"{key}::{user_id}" if user_id else key
        return hashlib.md5(raw.encode()).hexdigest()

    # ── Legacy message-based API (backward compatible) ──────────────────

    def get_by_message(self, message: str, user_id: str = "") -> Optional[str]:
        """Get cached response by message text (legacy)."""
        key = self._key(message, user_id)
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None:
                if entry.is_expired:
                    del self._cache[key]
                    return None
                self._cache.move_to_end(key)
                return entry.value
        return None

    def set_by_message(self, message: str, response: str, user_id: str = "",
                       ttl: int = None) -> None:
        """Cache a response by message text (legacy)."""
        key = self._key(message, user_id)
        actual_ttl = ttl if ttl is not None else self.default_ttl
        entry = CacheEntry(response, actual_ttl)
        with self._lock:
            while len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            self._cache[key] = entry

    # ── Utility methods ─────────────────────────────────────────────────

    def clear(self):
        """Clear all cached entries."""
        with self._lock:
            self._cache.clear()

    def remove(self, key: str, user_id: str = "") -> bool:
        """Remove a specific cache entry. Returns True if existed."""
        full_key = self._make_key(key, user_id)
        with self._lock:
            if full_key in self._cache:
                del self._cache[full_key]
                return True
        return False

    def clean_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        removed = 0
        now = time.time()
        with self._lock:
            expired_keys = [
                k for k, e in self._cache.items()
                if e.is_expired
            ]
            for k in expired_keys:
                del self._cache[k]
                removed += 1
        return removed

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)


# ── Global cache instance ────────────────────────────────────────────────────

# Shared singleton used across the application
_response_cache: Optional[ResponseCache] = None
_cache_lock = threading.Lock()


def get_cache() -> ResponseCache:
    """Get or create the global ResponseCache singleton."""
    global _response_cache
    if _response_cache is None:
        with _cache_lock:
            if _response_cache is None:
                _response_cache = ResponseCache()
    return _response_cache


def set_cache(cache: ResponseCache):
    """Set the global cache instance (used during lifespan init)."""
    global _response_cache
    _response_cache = cache


# ── Legacy helpers (backward compat) ─────────────────────────────────────────

CACHEABLE_PATTERNS = [
    "今日运势", "今日宜忌", "今日日历", "今天运势",
    "你好", "您好", "hi", "hello",
    "帮助", "help", "能做什么",
    "八字格式", "怎么用", "使用说明",
    "今日黄历", "黄历", "运势", "宜忌",
    "帮我看看", "帮我看", "怎么样", "如何",
]


def is_cacheable(message: str) -> bool:
    """Check if a message is eligible for caching."""
    msg = message.strip().lower()
    if len(msg) < 20:
        return True
    for pattern in CACHEABLE_PATTERNS:
        if pattern in msg:
            return True
    return False


# ── ChartCache (unchanged) ───────────────────────────────────────────────────

class ChartCache:
    """Cache for generated chart images. Keyed by bazi data hash."""

    def __init__(self, ttl: int = 86400):  # 24 hour TTL
        self._cache: Dict[str, Tuple[float, str]] = {}
        self._lock = threading.Lock()
        self.ttl = ttl

    def _key(self, bazi_tuple: tuple) -> str:
        return hashlib.md5(str(bazi_tuple).encode()).hexdigest()

    def get(self, year, month, day, hour, minute, city, gender) -> Optional[str]:
        key = self._key((year, month, day, hour, minute, city, gender))
        with self._lock:
            if key in self._cache:
                ts, path = self._cache[key]
                if time.time() - ts < self.ttl:
                    return path
                del self._cache[key]
        return None

    def set(self, year, month, day, hour, minute, city, gender, path: str):
        key = self._key((year, month, day, hour, minute, city, gender))
        with self._lock:
            self._cache[key] = (time.time(), path)
