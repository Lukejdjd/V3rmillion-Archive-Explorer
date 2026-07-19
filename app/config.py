import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ARCHIVE_DATA_DIR", ROOT_DIR / "data"))
HOST = os.environ.get("HOST", "localhost")
PORT = int(os.environ.get("PORT", "8000"))
WORKERS = int(os.environ.get("WEB_CONCURRENCY", os.environ.get("UVICORN_WORKERS", "2")))
IFRAMELY_BACKEND = os.environ.get("IFRAMELY_BACKEND", "http://localhost:8061").rstrip("/")
MANAGE_IFRAMELY = os.environ.get("MANAGE_IFRAMELY", "1").lower() not in {"0", "false", "no"}

# Rate limits (per IP, sliding window)
SEARCH_RATE_LIMIT = int(os.environ.get("SEARCH_RATE_LIMIT", "10"))
SEARCH_RATE_WINDOW = int(os.environ.get("SEARCH_RATE_WINDOW", "60"))
READ_RATE_LIMIT = int(os.environ.get("READ_RATE_LIMIT", "60"))
READ_RATE_WINDOW = int(os.environ.get("READ_RATE_WINDOW", "60"))
TURNSTILE_SOFT_LIMIT = int(os.environ.get("TURNSTILE_SOFT_LIMIT", "15"))
BLOCK_HARD_LIMIT = int(os.environ.get("BLOCK_HARD_LIMIT", "100"))
BLOCK_COOLDOWN_SECONDS = int(os.environ.get("BLOCK_COOLDOWN_SECONDS", "300"))
FAILED_REQUEST_LIMIT = int(os.environ.get("FAILED_REQUEST_LIMIT", "20"))
FAILED_REQUEST_WINDOW = int(os.environ.get("FAILED_REQUEST_WINDOW", "60"))

# Cloudflare Turnstile (optional; empty = verification disabled)
TURNSTILE_SITE_KEY = os.environ.get("TURNSTILE_SITE_KEY", "").strip()
TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "").strip()
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
# After a successful challenge, skip re-prompting this IP for a while
# (Turnstile tokens are single-use).
TURNSTILE_GRACE_SECONDS = int(os.environ.get("TURNSTILE_GRACE_SECONDS", "600"))

# In-memory response cache TTLs (seconds)
CACHE_TTL_SEARCH = int(os.environ.get("CACHE_TTL_SEARCH", "60"))
CACHE_TTL_THREAD = int(os.environ.get("CACHE_TTL_THREAD", "120"))
CACHE_TTL_USER = int(os.environ.get("CACHE_TTL_USER", "300"))
CACHE_TTL_STATIC = int(os.environ.get("CACHE_TTL_STATIC", "3600"))
CACHE_MAX_ENTRIES = int(os.environ.get("CACHE_MAX_ENTRIES", "2048"))

LOG_REQUESTS = os.environ.get("LOG_REQUESTS", "1").lower() not in {"0", "false", "no"}
