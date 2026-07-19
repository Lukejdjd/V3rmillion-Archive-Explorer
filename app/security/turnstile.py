"""Cloudflare Turnstile verification for suspicious search traffic."""

from __future__ import annotations

import logging

import httpx

from app.config import TURNSTILE_SECRET_KEY, TURNSTILE_VERIFY_URL

logger = logging.getLogger("archive.turnstile")


async def verify_turnstile(token: str | None, remote_ip: str | None = None) -> bool:
    """Return True when Turnstile is disabled or the token verifies."""
    if not TURNSTILE_SECRET_KEY:
        # Misconfigured soft-challenge: allow traffic rather than lock out the site.
        return True
    if not token:
        return False

    data = {
        "secret": TURNSTILE_SECRET_KEY,
        "response": token,
    }
    if remote_ip:
        data["remoteip"] = remote_ip

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(TURNSTILE_VERIFY_URL, data=data)
            payload = resp.json()
            return bool(payload.get("success"))
    except Exception as exc:
        logger.warning("Turnstile verification failed: %s", exc)
        return False
