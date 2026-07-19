"""Simple process-local TTL cache for popular API responses."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any

from app.config import CACHE_MAX_ENTRIES


class ResponseCache:
    def __init__(self, max_entries: int = CACHE_MAX_ENTRIES):
        self._max = max(64, max_entries)
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def make_key(prefix: str, params: dict) -> str:
        payload = json.dumps(params, sort_keys=True, default=str, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
        return f"{prefix}:{digest}"

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at < now:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        if ttl <= 0:
            return
        expires_at = time.monotonic() + ttl
        with self._lock:
            self._data[key] = (expires_at, value)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_cache = ResponseCache()


def get_cache() -> ResponseCache:
    return _cache
