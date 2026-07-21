"""Shared sliding-window rate limiting across Uvicorn workers via SQLite."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

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
    "/api/user_reputation",
}

RATE_LIMIT_DB = Path(
    os.environ.get(
        "RATE_LIMIT_DB",
        str(Path(tempfile.gettempdir()) / "archive-ratelimit.db"),
    )
)


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
    """Process-safe limiter: all Uvicorn workers share one SQLite file."""

    def __init__(self, db_path: Path = RATE_LIMIT_DB) -> None:
        self._db_path = Path(db_path)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        if not getattr(self._local, "conn", None):
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self._db_path),
                timeout=5.0,
                isolation_level=None,  # autocommit; we use explicit BEGIN
                check_same_thread=False,
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
            self._ensure_schema(conn)
        return self._local.conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    kind TEXT NOT NULL,
                    ip TEXT NOT NULL,
                    ts REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_kind_ip_ts "
                "ON events(kind, ip, ts)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flags (
                    ip TEXT NOT NULL,
                    name TEXT NOT NULL,
                    until_ts REAL NOT NULL,
                    PRIMARY KEY (ip, name)
                )
                """
            )
            self._initialized = True

    def _prune(self, conn: sqlite3.Connection, kind: str, ip: str, cutoff: float) -> None:
        conn.execute(
            "DELETE FROM events WHERE kind = ? AND ip = ? AND ts < ?",
            (kind, ip, cutoff),
        )

    def _count(self, conn: sqlite3.Connection, kind: str, ip: str, window: float, now: float) -> int:
        cutoff = now - window
        self._prune(conn, kind, ip, cutoff)
        row = conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind = ? AND ip = ? AND ts >= ?",
            (kind, ip, cutoff),
        ).fetchone()
        return int(row[0] if row else 0)

    def _add(self, conn: sqlite3.Connection, kind: str, ip: str, now: float) -> None:
        conn.execute(
            "INSERT INTO events(kind, ip, ts) VALUES (?, ?, ?)",
            (kind, ip, now),
        )

    def _flag_until(self, conn: sqlite3.Connection, ip: str, name: str) -> float:
        row = conn.execute(
            "SELECT until_ts FROM flags WHERE ip = ? AND name = ?",
            (ip, name),
        ).fetchone()
        return float(row[0]) if row else 0.0

    def _set_flag(self, conn: sqlite3.Connection, ip: str, name: str, until_ts: float) -> None:
        conn.execute(
            "INSERT INTO flags(ip, name, until_ts) VALUES (?, ?, ?) "
            "ON CONFLICT(ip, name) DO UPDATE SET until_ts = excluded.until_ts",
            (ip, name, until_ts),
        )

    def record_failure(self, ip: str) -> None:
        now = time.time()
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._add(conn, "failed", ip, now)
            count = self._count(conn, "failed", ip, FAILED_REQUEST_WINDOW, now)
            if count >= FAILED_REQUEST_LIMIT:
                self._set_flag(conn, ip, "blocked", now + BLOCK_COOLDOWN_SECONDS)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def mark_honeypot(self, ip: str) -> None:
        now = time.time()
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._set_flag(conn, ip, "blocked", now + BLOCK_COOLDOWN_SECONDS)
            conn.execute(
                "DELETE FROM flags WHERE ip = ? AND name = ?",
                (ip, "turnstile"),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def mark_turnstile_passed(self, ip: str, grace: int | None = None) -> None:
        seconds = TURNSTILE_GRACE_SECONDS if grace is None else grace
        now = time.time()
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._set_flag(conn, ip, "turnstile", now + max(30, seconds))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def check(self, ip: str, path: str) -> RateDecision:
        now = time.time()
        turnstile_enabled = bool(TURNSTILE_SECRET_KEY)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            blocked_until = self._flag_until(conn, ip, "blocked")
            if blocked_until > now:
                conn.execute("COMMIT")
                return RateDecision(
                    allowed=False,
                    reason="ip_cooldown",
                    retry_after=max(1, int(blocked_until - now)),
                )

            if path in SEARCH_PATHS:
                count = self._count(conn, "search", ip, SEARCH_RATE_WINDOW, now)
                recently_verified = self._flag_until(conn, ip, "turnstile") > now

                if count >= BLOCK_HARD_LIMIT:
                    self._set_flag(conn, ip, "blocked", now + BLOCK_COOLDOWN_SECONDS)
                    conn.execute("COMMIT")
                    return RateDecision(
                        allowed=False,
                        reason="rate_limit_block",
                        retry_after=BLOCK_COOLDOWN_SECONDS,
                        search_count=count,
                    )

                if count >= SEARCH_RATE_LIMIT:
                    oldest = conn.execute(
                        "SELECT MIN(ts) FROM events WHERE kind = 'search' AND ip = ? "
                        "AND ts >= ?",
                        (ip, now - SEARCH_RATE_WINDOW),
                    ).fetchone()
                    oldest_ts = float(oldest[0]) if oldest and oldest[0] is not None else now
                    conn.execute("COMMIT")
                    return RateDecision(
                        allowed=False,
                        reason="rate_limit",
                        retry_after=max(1, int(SEARCH_RATE_WINDOW - (now - oldest_ts))),
                        search_count=count,
                    )

                soft = min(TURNSTILE_SOFT_LIMIT, max(0, SEARCH_RATE_LIMIT - 1))
                self._add(conn, "search", ip, now)
                conn.execute("COMMIT")
                if turnstile_enabled and count >= soft and not recently_verified:
                    return RateDecision(
                        allowed=True,
                        require_turnstile=True,
                        search_count=count + 1,
                    )
                return RateDecision(
                    allowed=True,
                    require_turnstile=False,
                    search_count=count + 1,
                )

            if path in READ_PATHS:
                count = self._count(conn, "read", ip, READ_RATE_WINDOW, now)
                if count >= READ_RATE_LIMIT:
                    oldest = conn.execute(
                        "SELECT MIN(ts) FROM events WHERE kind = 'read' AND ip = ? "
                        "AND ts >= ?",
                        (ip, now - READ_RATE_WINDOW),
                    ).fetchone()
                    oldest_ts = float(oldest[0]) if oldest and oldest[0] is not None else now
                    conn.execute("COMMIT")
                    return RateDecision(
                        allowed=False,
                        reason="rate_limit",
                        retry_after=max(1, int(READ_RATE_WINDOW - (now - oldest_ts))),
                    )
                self._add(conn, "read", ip, now)
                conn.execute("COMMIT")
                return RateDecision(allowed=True)

            conn.execute("COMMIT")
            return RateDecision(allowed=True)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise


_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _limiter
