"""Static index JSON endpoints (usergroups, awards, smilies)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException

from app.cache import get_cache
from app.config import CACHE_TTL_STATIC
from app.database.sqlite import index_path

router = APIRouter(tags=["profiles"])


def _load_index(filename: str) -> Any:
    cache = get_cache()
    cache_key = f"index:{filename}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    path = index_path(filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")

    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    cache.set(cache_key, payload, CACHE_TTL_STATIC)
    return payload


@router.get("/usergroups")
def usergroups() -> Any:
    return _load_index("usergroups.json")


@router.get("/awards")
def awards() -> Any:
    return _load_index("awards.json")


@router.get("/smilies")
def smilies() -> Any:
    return _load_index("smilies.json")
