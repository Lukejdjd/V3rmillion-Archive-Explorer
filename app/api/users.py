"""User profile and user-post endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.cache import get_cache
from app.config import CACHE_TTL_USER
from app.database.queries import (
    bounded_int,
    enrich_post_row,
    parse_awards_field,
    parse_json_field,
    sanitize_fts_query,
)
from app.database.sqlite import connect_archive, threads_db_path, users_db_path

router = APIRouter(tags=["users"])


@router.get("/user")
def get_user(uid: str = Query("")) -> dict[str, Any]:
    if not uid:
        raise HTTPException(status_code=400, detail="uid required")

    cache = get_cache()
    cache_key = cache.make_key("user", {"uid": uid})
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    db_path = users_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=500, detail="users.db not found")

    conn = connect_archive(db_path)
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA table_info(users)")
        cols = {row["name"] for row in cur.fetchall()}
        uid_col = "user_id" if "user_id" in cols else "uid"

        cur.execute(f"SELECT * FROM users WHERE {uid_col} = ?", (uid,))
        row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="user not found")

    user = dict(row)
    user.pop("past_usernames_search", None)
    user["awards"] = parse_awards_field(user.get("awards"))
    if "user_group" in user:
        user["user_group"] = parse_json_field(user.get("user_group"))
    if "past_usernames" in user:
        user["past_usernames"] = parse_json_field(user.get("past_usernames")) or []

    payload = {"user": user}
    cache.set(cache_key, payload, CACHE_TTL_USER)
    return payload


@router.get("/user_posts")
def user_posts(
    uid: str = Query(""),
    op_only: str = Query("0"),
    offset: int = Query(0),
    limit: int = Query(50),
) -> dict[str, Any]:
    if not uid:
        raise HTTPException(status_code=400, detail="uid required")

    op_only_flag = op_only == "1"
    offset = bounded_int(offset, 0, 0, 100_000_000)
    limit = bounded_int(limit, 50, 1, 100)

    cache = get_cache()
    cache_key = cache.make_key(
        "user_posts",
        {"uid": uid, "op_only": op_only_flag, "offset": offset, "limit": limit},
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
        select_fields = "p.*, t.title AS thread_title"
        left_join_user = ""
        op_filter = "AND p.is_op = 1" if op_only_flag else ""
        sql = f"""SELECT {select_fields} FROM posts p
                  LEFT JOIN threads t ON t.thread_id = p.thread_id
                  {left_join_user}
                  WHERE p.author_id = ? {op_filter}
                  ORDER BY CAST(p.unix_time AS INTEGER) DESC, p.post_number DESC
                  LIMIT ? OFFSET ?"""
        cur.execute(sql, (uid, limit, offset))
        posts = [enrich_post_row(dict(r)) for r in cur.fetchall()]
    finally:
        conn.close()

    payload = {"posts": posts}
    cache.set(cache_key, payload, CACHE_TTL_USER)
    return payload


@router.get("/search_users")
def search_users(
    q: str = Query(""),
    sort: str = Query("relevance"),
    order: str = Query("desc"),
    offset: int = Query(0),
    limit: int = Query(24),
) -> dict[str, Any]:
    q = (q or "").strip()
    offset = bounded_int(offset, 0, 0, 100_000_000)
    limit = bounded_int(limit, 24, 1, 100)

    cache = get_cache()
    cache_key = cache.make_key(
        "search_users",
        {"q": q, "sort": sort, "order": order, "offset": offset, "limit": limit},
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    db_path = users_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=500, detail="users.db not found")

    conn = connect_archive(db_path)
    cur = conn.cursor()

    cur.execute("SELECT sql FROM sqlite_master WHERE name='fts_users'")
    fts_schema = cur.fetchone()
    has_fts = fts_schema is not None
    fts_join = (
        "u.rowid = f.rowid"
        if fts_schema and "content='users'" in (fts_schema[0] or "")
        else "u.user_id = f.user_id"
    )

    dir_sql = "ASC" if order == "asc" else "DESC"
    if sort == "relevance" and not q:
        sort = "user_id"

    params: list[Any] = []

    try:
        if has_fts and q:
            sanitized_q = sanitize_fts_query(q)
            base = (
                "SELECT u.user_id, u.username, u.user_title, u.user_rank, "
                "u.reputation, u.post_count, u.thread_count, u.user_stars, "
                "u.user_group, u.joined, u.awards, u.past_usernames "
                f"FROM fts_users f JOIN users u ON {fts_join} "
                "WHERE fts_users MATCH ?"
            )
            params.append(sanitized_q)
        else:
            base = (
                "SELECT user_id, username, user_title, user_rank, "
                "reputation, post_count, thread_count, user_stars, "
                "user_group, joined, awards, past_usernames FROM users WHERE 1=1"
            )

        t = "u." if (has_fts and q) else ""
        if sort == "relevance" and q and has_fts:
            pass
        elif sort == "user_id":
            base += f" ORDER BY CAST({t}user_id AS INTEGER) {dir_sql}"
        elif sort == "username":
            base += f" ORDER BY {t}username {dir_sql}"
        elif sort == "post_count":
            base += f" ORDER BY CAST({t}post_count AS INTEGER) {dir_sql}"
        elif sort == "reputation":
            base += f" ORDER BY CAST({t}reputation AS INTEGER) {dir_sql}"
        elif sort == "joined":
            base += f" ORDER BY {t}joined {dir_sql}"

        try:
            cur.execute(f"SELECT COUNT(*) FROM ({base})", params)
            total = cur.fetchone()[0]
        except Exception:
            total = None

        base += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        try:
            cur.execute(base, params)
            rows = cur.fetchall()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

        out = []
        for r in rows:
            out.append(
                {
                    "user_id": str(r["user_id"]),
                    "username": r["username"] or "",
                    "user_title": r["user_title"] or "",
                    "user_rank": r["user_rank"] or "",
                    "reputation": r["reputation"] or "",
                    "post_count": r["post_count"] or "",
                    "thread_count": r["thread_count"] or "",
                    "user_stars": r["user_stars"] or 0,
                    "user_group": parse_json_field(r["user_group"]),
                    "joined": r["joined"] or "",
                    "awards": parse_awards_field(r["awards"]),
                    "past_usernames": parse_json_field(r["past_usernames"]) or [],
                }
            )
    finally:
        conn.close()

    payload = {"results": out, "total": total, "offset": offset, "limit": limit}
    cache.set(cache_key, payload, CACHE_TTL_USER)
    return payload
