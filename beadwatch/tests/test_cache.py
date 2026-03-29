import time
from unittest.mock import patch

from utils.cache import TTLCache


class TestTTLCache:
    def test_put_and_get_hit(self):
        cache = TTLCache(default_ttl=10.0)
        cache.put("k1", {"data": 42})
        assert cache.get("k1") == {"data": 42}

    def test_get_miss_returns_none(self):
        cache = TTLCache()
        assert cache.get("nonexistent") is None

    def test_expiry(self):
        cache = TTLCache(default_ttl=0.05)
        cache.put("k1", "val")
        assert cache.get("k1") == "val"
        time.sleep(0.06)
        assert cache.get("k1") is None

    def test_per_entry_ttl_override(self):
        cache = TTLCache(default_ttl=10.0)
        cache.put("short", "val", ttl=0.05)
        cache.put("long", "val", ttl=10.0)
        time.sleep(0.06)
        assert cache.get("short") is None
        assert cache.get("long") == "val"

    def test_invalidate(self):
        cache = TTLCache()
        cache.put("k1", "val")
        cache.invalidate("k1")
        assert cache.get("k1") is None

    def test_invalidate_missing_key_no_error(self):
        cache = TTLCache()
        cache.invalidate("missing")  # should not raise

    def test_clear(self):
        cache = TTLCache()
        cache.put("a", 1)
        cache.put("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert len(cache) == 0

    def test_eviction_when_over_max(self):
        cache = TTLCache(default_ttl=60.0, max_entries=3)
        cache.put("a", 1, ttl=10.0)
        cache.put("b", 2, ttl=20.0)
        cache.put("c", 3, ttl=30.0)
        assert len(cache) == 3
        # Adding a 4th entry should evict the one with the earliest expiry ("a")
        cache.put("d", 4, ttl=40.0)
        assert len(cache) == 3
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("d") == 4

    def test_expired_entries_evicted_before_live_ones(self):
        """When over max, expired entries are purged first."""
        cache = TTLCache(default_ttl=0.05, max_entries=2)
        cache.put("old", "val")
        time.sleep(0.06)
        # "old" is now expired; adding two more should stay within max
        cache.put("new1", "v1", ttl=60.0)
        cache.put("new2", "v2", ttl=60.0)
        assert len(cache) == 2
        assert cache.get("new1") == "v1"
        assert cache.get("new2") == "v2"

    def test_overwrite_existing_key(self):
        cache = TTLCache()
        cache.put("k1", "first")
        cache.put("k1", "second")
        assert cache.get("k1") == "second"

    def test_len(self):
        cache = TTLCache()
        assert len(cache) == 0
        cache.put("a", 1)
        cache.put("b", 2)
        assert len(cache) == 2
