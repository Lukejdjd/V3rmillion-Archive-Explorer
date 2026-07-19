"""Thread page endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.cache import get_cache
from app.config import CACHE_TTL_THREAD
from app.database.queries import bounded_int, enrich_post_row
from app.database.sqlite import connect_archive, threads_db_path

router = APIRouter(tags=["threads"])


@router.get("/thread")
def get_thread(
    thread_id: str = Query(""),
    limit: int | None = Query(None),
    offset: int = Query(0),
    post_id: str | None = None,
) -> dict[str, Any]:
    if not thread_id:
        raise HTTPException(status_code=400, detail="thread_id required")

    offset = bounded_int(offset, 0, 0, 100_000_000)
    limit_val = bounded_int(limit, 50, 1, 100) if limit is not None else None

    cache = get_cache()
    cache_key = cache.make_key(
        "thread",
        {
            "thread_id": thread_id,
            "limit": limit_val,
            "offset": offset,
            "post_id": post_id,
        },
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    db_path = threads_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=500, detail="threads.db not found")

    conn = connect_archive(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM threads WHERE thread_id = ?", (thread_id,))
        thread = cur.fetchone()
        if not thread:
            raise HTTPException(status_code=404, detail="not found")

        select_fields = "p.*"
        left_join_user = ""

        cur.execute("SELECT COUNT(*) FROM posts WHERE thread_id = ?", (thread_id,))
        total_posts = cur.fetchone()[0]

        if limit_val is not None and post_id:
            cur.execute(
                """SELECT row_number FROM (
                       SELECT post_id, ROW_NUMBER() OVER (
                           ORDER BY CASE WHEN unix_time IS NULL OR unix_time = '' THEN 1 ELSE 0 END,
                                    CAST(unix_time AS INTEGER) ASC, post_number ASC
                       ) AS row_number
                       FROM posts WHERE thread_id = ?
                   ) WHERE post_id = ?""",
                (thread_id, post_id),
            )
            jump_row = cur.fetchone()
            if jump_row:
                offset = ((jump_row[0] - 1) // limit_val) * limit_val

        sql = f"""SELECT {select_fields} FROM posts p
                  {left_join_user}
                  WHERE p.thread_id = ?
                  ORDER BY CASE WHEN unix_time IS NULL OR unix_time = '' THEN 1 ELSE 0 END,
                  CAST(unix_time AS INTEGER) ASC, post_number ASC"""

        params: list[Any] = [thread_id]
        if limit_val is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit_val, offset])
        cur.execute(sql, params)
        posts = [enrich_post_row(dict(r)) for r in cur.fetchall()]
    finally:
        conn.close()

    payload = {
        "thread": dict(thread),
        "posts": posts,
        "total": total_posts,
        "offset": offset,
        "limit": limit_val or total_posts,
    }
    cache.set(cache_key, payload, CACHE_TTL_THREAD)
    return payload
