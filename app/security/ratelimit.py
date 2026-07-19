"""In-memory sliding-window rate limiting and abuse cooldowns."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque

from fastapi import Request

from app.config import (
    BLOCK_COOLDOWN_SECONDS,
    BLOCK_HARD_LIMIT,
    FAILED_REQUEST_LIMIT,
    FAILED_REQUEST_WINDOW,
    READ_RATE_LIMIT,
    READ_RATE_WINDOW,
    SEARCH_RATE_LIMIT,
    SEARCH_RATE_WINDOW,
    TURNSTILE_GRACE_SECONDS,
    TURNSTILE_SECRET_KEY,
    TURNSTILE_SOFT_LIMIT,
)


SEARCH_PATHS = {
    "/api/search_posts",
    "/api/search_threads",
    "/api/search_users",
    "/api/user_posts",
}

READ_PATHS = {
    "/api/thread",
    "/api/user",
}


@dataclass
class RateDecision:
    allowed: bool
    reason: str = ""
    retry_after: int = 0
    require_turnstile: bool = False
    search_count: int = 0


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get(
        "x-forwarded-for"
    )
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._search: dict[str, Deque[float]] = defaultdict(deque)
        self._read: dict[str, Deque[float]] = defaultdict(deque)
        self._failed: dict[str, Deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}
        self._turnstile_ok_until: dict[str, float] = {}

    def _prune(self, bucket: Deque[float], window: float, now: float) -> None:
        while bucket and now - bucket[0] > window:
            bucket.popleft()

    def record_failure(self, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            bucket = self._failed[ip]
            bucket.append(now)
            self._prune(bucket, FAILED_REQUEST_WINDOW, now)
            if len(bucket) >= FAILED_REQUEST_LIMIT:
                self._blocked_until[ip] = now + BLOCK_COOLDOWN_SECONDS

    def mark_honeypot(self, ip: str) -> None:
        with self._lock:
            self._blocked_until[ip] = time.monotonic() + BLOCK_COOLDOWN_SECONDS
            self._turnstile_ok_until.pop(ip, None)

    def mark_turnstile_passed(self, ip: str, grace: int | None = None) -> None:
        seconds = TURNSTILE_GRACE_SECONDS if grace is None else grace
        with self._lock:
            self._turnstile_ok_until[ip] = time.monotonic() + max(30, seconds)

    def check(self, ip: str, path: str) -> RateDecision:
        now = time.monotonic()
        turnstile_enabled = bool(TURNSTILE_SECRET_KEY)

        with self._lock:
            blocked_until = self._blocked_until.get(ip, 0.0)
            if blocked_until > now:
                return RateDecision(
                    allowed=False,
                    reason="ip_cooldown",
                    retry_after=max(1, int(blocked_until - now)),
                )

            if path in SEARCH_PATHS:
                bucket = self._search[ip]
                self._prune(bucket, SEARCH_RATE_WINDOW, now)
                count = len(bucket)
                recently_verified = self._turnstile_ok_until.get(ip, 0.0) > now

                if count >= BLOCK_HARD_LIMIT:
                    self._blocked_until[ip] = now + BLOCK_COOLDOWN_SECONDS
                    return RateDecision(
                        allowed=False,
                        reason="rate_limit_block",
                        retry_after=BLOCK_COOLDOWN_SECONDS,
                        search_count=count,
                    )

                # Without Turnstile: hard stop at SEARCH_RATE_LIMIT (default 10/min).
                # With Turnstile: free until TURNSTILE_SOFT_LIMIT (default 30/min),
                # then require a challenge until BLOCK_HARD_LIMIT (default 100/min).
                if not turnstile_enabled and count >= SEARCH_RATE_LIMIT:
                    return RateDecision(
                        allowed=False,
                        reason="rate_limit",
                        retry_after=max(
                            1, int(SEARCH_RATE_WINDOW - (now - bucket[0]))
                        ),
                        search_count=count,
                    )

                if (
                    turnstile_enabled
                    and count >= TURNSTILE_SOFT_LIMIT
                    and not recently_verified
                ):
                    bucket.append(now)
                    return RateDecision(
                        allowed=True,
                        require_turnstile=True,
                        search_count=count + 1,
                    )

                bucket.append(now)
                return RateDecision(
                    allowed=True,
                    require_turnstile=False,
                    search_count=count + 1,
                )

            if path in READ_PATHS:
                bucket = self._read[ip]
                self._prune(bucket, READ_RATE_WINDOW, now)
                if len(bucket) >= READ_RATE_LIMIT:
                    return RateDecision(
                        allowed=False,
                        reason="rate_limit",
                        retry_after=max(
                            1, int(READ_RATE_WINDOW - (now - bucket[0]))
                        ),
                    )
                bucket.append(now)
                return RateDecision(allowed=True)

            return RateDecision(allowed=True)


_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _limiter
