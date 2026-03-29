import threading
import time
from typing import Any, Optional


class TTLCache:
    """Thread-safe in-memory cache with per-entry TTL expiry.

    Parameters:
        default_ttl: Seconds before an entry expires (default 120).
        max_entries: Maximum number of cached entries (default 50).
                     When exceeded, the oldest entry is evicted.
    """

    def __init__(self, default_ttl: float = 120.0, max_entries: int = 50):
        self._ttl = default_ttl
        self._max = max_entries
        self._lock = threading.Lock()
        # key -> (value, expiry_monotonic)
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Optional[Any]:
        """Return cached value or None if missing/expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if time.monotonic() > expiry:
                del self._store[key]
                return None
            return value

    def put(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Store a value with optional per-entry TTL override."""
        expiry = time.monotonic() + (ttl if ttl is not None else self._ttl)
        with self._lock:
            self._store[key] = (value, expiry)
            self._evict_if_needed()

    def invalidate(self, key: str) -> None:
        """Remove a specific key from the cache."""
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._store.clear()

    def _evict_if_needed(self) -> None:
        """Evict expired entries first, then oldest if still over max."""
        now = time.monotonic()
        # Purge expired
        expired = [k for k, (_, exp) in self._store.items() if now > exp]
        for k in expired:
            del self._store[k]
        # If still over capacity, drop oldest by expiry time
        while len(self._store) > self._max:
            oldest_key = min(self._store, key=lambda k: self._store[k][1])
            del self._store[oldest_key]

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
