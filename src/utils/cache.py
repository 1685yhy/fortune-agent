"""Response cache with TTL — reduces latency for high-frequency requests.

D2 speed optimization. Caches common queries and chart images to cut
response time from 3-10s to <1s for 30%+ of requests.
"""
import hashlib
import time
import threading
from typing import Dict, Optional, Tuple
from collections import OrderedDict


class ResponseCache:
    """Thread-safe in-memory cache with TTL and LRU eviction.

    Uses OrderedDict for LRU behavior — oldest entries evicted when full.
    """

    def __init__(self, max_size: int = 500, default_ttl: int = 3600):
        self._cache: OrderedDict[str, Tuple[float, str]] = OrderedDict()
        self._lock = threading.Lock()
        self.max_size = max_size
        self.default_ttl = default_ttl  # 1 hour default

    def _key(self, message: str, user_id: str = "") -> str:
        """Generate a cache key from message content."""
        # Normalize: lowercase, strip whitespace
        normalized = message.strip().lower()
        # Only cache exact matches (not similar)
        return hashlib.md5(normalized.encode()).hexdigest()

    def get(self, message: str, user_id: str = "") -> Optional[str]:
        """Get cached response. Returns None if miss or expired."""
        key = self._key(message, user_id)
        with self._lock:
            if key in self._cache:
                timestamp, response = self._cache[key]
                if time.time() - timestamp < self.default_ttl:
                    # Move to end (most recently used)
                    self._cache.move_to_end(key)
                    return response
                else:
                    # Expired
                    del self._cache[key]
        return None

    def set(self, message: str, response: str, user_id: str = "",
            ttl: int = None):
        """Cache a response with TTL."""
        key = self._key(message, user_id)
        with self._lock:
            # Evict oldest if at capacity
            while len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            self._cache[key] = (time.time(), response)

    def clear(self):
        """Clear all cached entries."""
        with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)


# High-frequency messages that should ALWAYS be cached
CACHEABLE_PATTERNS = [
    "今日运势", "今日宜忌", "今日日历", "今天运势",
    "你好", "您好", "hi", "hello",
    "帮助", "help", "能做什么",
    "八字格式", "怎么用", "使用说明",
    "今日黄历", "黄历",
]


def is_cacheable(message: str) -> bool:
    """Check if a message is eligible for caching."""
    msg = message.strip().lower()
    # Cache short high-frequency queries
    if len(msg) < 20:
        return True
    # Cache known patterns
    for pattern in CACHEABLE_PATTERNS:
        if pattern in msg:
            return True
    # Don't cache personal/unique queries (birth dates, long stories)
    return False


# Chart image cache (same bazi data → same chart for 24h)
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
