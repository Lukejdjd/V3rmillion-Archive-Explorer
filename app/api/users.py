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
    sanitize_fts_prefix_query,
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
        reputation_history_count = 0
        history_table = cur.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='reputation_history'"
        ).fetchone()
        if history_table:
            reputation_history_count = cur.execute(
                "SELECT COUNT(*) FROM reputation_history WHERE user_id = ?",
                (uid,),
            ).fetchone()[0]
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
    user["reputation_history_count"] = reputation_history_count

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
    by: str = Query("username"),
    sort: str = Query("username"),
    order: str = Query("desc"),
    offset: int = Query(0),
    limit: int = Query(24),
    min_rep: str | None = None,
    max_rep: str | None = None,
    min_posts: str | None = None,
    max_posts: str | None = None,
    min_threads: str | None = None,
    max_threads: str | None = None,
) -> dict[str, Any]:
    q = (q or "").strip()
    by = (by or "username").strip().lower()
    if by not in {"username", "user_id"}:
        by = "username"
    offset = bounded_int(offset, 0, 0, 100_000_000)
    limit = bounded_int(limit, 24, 1, 100)

    def _optional_int(raw: str | None) -> int | None:
        if raw in (None, ""):
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    min_rep_i = _optional_int(min_rep)
    max_rep_i = _optional_int(max_rep)
    min_posts_i = _optional_int(min_posts)
    max_posts_i = _optional_int(max_posts)
    min_threads_i = _optional_int(min_threads)
    max_threads_i = _optional_int(max_threads)

    cache = get_cache()
    cache_key = cache.make_key(
        "search_users",
        {
            "q": q,
            "by": by,
            "sort": sort,
            "order": order,
            "offset": offset,
            "limit": limit,
            "min_rep": min_rep_i,
            "max_rep": max_rep_i,
            "min_posts": min_posts_i,
            "max_posts": max_posts_i,
            "min_threads": min_threads_i,
            "max_threads": max_threads_i,
        },
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
    if sort == "relevance" and (not q or by == "user_id"):
        sort = "username"

    def append_numeric_filters(sql: str, alias: str = "") -> tuple[str, list[Any]]:
        prefix = f"{alias}." if alias else ""
        extra: list[Any] = []
        clauses = []
        if min_rep_i is not None:
            clauses.append(f"CAST({prefix}reputation AS INTEGER) >= ?")
            extra.append(min_rep_i)
        if max_rep_i is not None:
            clauses.append(f"CAST({prefix}reputation AS INTEGER) <= ?")
            extra.append(max_rep_i)
        if min_posts_i is not None:
            clauses.append(f"CAST({prefix}post_count AS INTEGER) >= ?")
            extra.append(min_posts_i)
        if max_posts_i is not None:
            clauses.append(f"CAST({prefix}post_count AS INTEGER) <= ?")
            extra.append(max_posts_i)
        if min_threads_i is not None:
            clauses.append(f"CAST({prefix}thread_count AS INTEGER) >= ?")
            extra.append(min_threads_i)
        if max_threads_i is not None:
            clauses.append(f"CAST({prefix}thread_count AS INTEGER) <= ?")
            extra.append(max_threads_i)
        if clauses:
            sql += " AND " + " AND ".join(clauses)
        return sql, extra

    params: list[Any] = []
    rows = []
    total = None

    try:
        used_fts = False

        if by == "user_id":
            base = (
                "SELECT user_id, username, user_title, user_rank, "
                "reputation, post_count, thread_count, user_stars, "
                "user_group, joined, awards, past_usernames FROM users WHERE 1=1"
            )
            params = []
            if q:
                base += " AND CAST(user_id AS TEXT) = ?"
                params.append(q)
            base, extra = append_numeric_filters(base)
            params.extend(extra)
        elif has_fts and q:
            prefix_q = sanitize_fts_prefix_query(q)
            if prefix_q:
                base = (
                    "SELECT u.user_id, u.username, u.user_title, u.user_rank, "
                    "u.reputation, u.post_count, u.thread_count, u.user_stars, "
                    "u.user_group, u.joined, u.awards, u.past_usernames "
                    f"FROM fts_users f JOIN users u ON {fts_join} "
                    "WHERE fts_users MATCH ?"
                )
                params = [prefix_q]
                base, extra = append_numeric_filters(base, "u")
                params.extend(extra)
                used_fts = True
            else:
                base = ""
        else:
            base = ""

        if by != "user_id" and not used_fts:
            base = (
                "SELECT user_id, username, user_title, user_rank, "
                "reputation, post_count, thread_count, user_stars, "
                "user_group, joined, awards, past_usernames FROM users WHERE 1=1"
            )
            params = []
            if q:
                like = f"%{q}%"
                base += (
                    " AND (username LIKE ? COLLATE NOCASE "
                    "OR COALESCE(past_usernames_search, '') LIKE ? COLLATE NOCASE "
                    "OR COALESCE(user_title, '') LIKE ? COLLATE NOCASE)"
                )
                params.extend([like, like, like])
            base, extra = append_numeric_filters(base)
            params.extend(extra)

        # If FTS returned nothing useful for short prefixes, fall back to LIKE.
        def apply_sort(sql: str, aliased: bool) -> str:
            t = "u." if aliased else ""
            if sort == "relevance" and aliased and q:
                return sql
            if sort == "user_id":
                return sql + f" ORDER BY CAST({t}user_id AS INTEGER) {dir_sql}"
            if sort == "username":
                return sql + f" ORDER BY {t}username COLLATE NOCASE {dir_sql}"
            if sort == "post_count":
                return sql + f" ORDER BY CAST({t}post_count AS INTEGER) {dir_sql}"
            if sort == "thread_count":
                return sql + f" ORDER BY CAST({t}thread_count AS INTEGER) {dir_sql}"
            if sort == "reputation":
                return sql + f" ORDER BY CAST(NULLIF({t}reputation, '') AS INTEGER) {dir_sql} NULLS LAST"
            if sort == "joined":
                return sql + (
                    f" ORDER BY CASE"
                    f" WHEN LENGTH({t}joined) = 10"
                    f" THEN CAST(SUBSTR({t}joined,7,4) AS INTEGER) * 10000"
                    f" + CAST(SUBSTR({t}joined,1,2) AS INTEGER) * 100"
                    f" + CAST(SUBSTR({t}joined,4,2) AS INTEGER)"
                    f" WHEN {t}joined = 'Today' THEN 99999999"
                    f" WHEN {t}joined = 'Yesterday' THEN 99999998"
                    f" ELSE NULL END {dir_sql} NULLS LAST"
                )
            return sql + f" ORDER BY CAST({t}post_count AS INTEGER) {dir_sql}"

        aliased = used_fts
        sorted_sql = apply_sort(base, aliased)

        try:
            cur.execute(f"SELECT COUNT(*) FROM ({base})", params)
            total = cur.fetchone()[0]
        except Exception:
            total = None

        query_sql = sorted_sql + " LIMIT ? OFFSET ?"
        query_params = params + [limit, offset]

        try:
            cur.execute(query_sql, query_params)
            rows = cur.fetchall()
        except Exception as e:
            # FTS can fail on odd tokens; fall back to LIKE once.
            if used_fts:
                like = f"%{q}%"
                base = (
                    "SELECT user_id, username, user_title, user_rank, "
                    "reputation, post_count, thread_count, user_stars, "
                    "user_group, joined, awards, past_usernames FROM users WHERE 1=1 "
                    "AND (username LIKE ? COLLATE NOCASE "
                    "OR COALESCE(past_usernames_search, '') LIKE ? COLLATE NOCASE "
                    "OR COALESCE(user_title, '') LIKE ? COLLATE NOCASE)"
                )
                params = [like, like, like]
                base, extra = append_numeric_filters(base)
                params.extend(extra)
                sorted_sql = apply_sort(base, False)
                try:
                    cur.execute(f"SELECT COUNT(*) FROM ({base})", params)
                    total = cur.fetchone()[0]
                except Exception:
                    total = None
                cur.execute(sorted_sql + " LIMIT ? OFFSET ?", params + [limit, offset])
                rows = cur.fetchall()
            else:
                raise HTTPException(status_code=500, detail=str(e)) from e

        # If prefix FTS found nothing, try substring LIKE (covers mid-name matches).
        if used_fts and q and not rows:
            like = f"%{q}%"
            base = (
                "SELECT user_id, username, user_title, user_rank, "
                "reputation, post_count, thread_count, user_stars, "
                "user_group, joined, awards, past_usernames FROM users WHERE 1=1 "
                "AND (username LIKE ? COLLATE NOCASE "
                "OR COALESCE(past_usernames_search, '') LIKE ? COLLATE NOCASE "
                "OR COALESCE(user_title, '') LIKE ? COLLATE NOCASE)"
            )
            params = [like, like, like]
            base, extra = append_numeric_filters(base)
            params.extend(extra)
            sorted_sql = apply_sort(base, False)
            try:
                cur.execute(f"SELECT COUNT(*) FROM ({base})", params)
                total = cur.fetchone()[0]
            except Exception:
                total = None
            cur.execute(sorted_sql + " LIMIT ? OFFSET ?", params + [limit, offset])
            rows = cur.fetchall()

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


@router.get("/user_reputation")
def user_reputation(
    uid: str = Query(""),
    offset: int = Query(0),
    limit: int = Query(25),
) -> dict[str, Any]:
    if not uid:
        raise HTTPException(status_code=400, detail="uid required")

    offset = bounded_int(offset, 0, 0, 100_000_000)
    limit = bounded_int(limit, 25, 1, 100)

    cache = get_cache()
    cache_key = cache.make_key(
        "user_reputation",
        {"uid": uid, "offset": offset, "limit": limit},
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    db_path = users_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=500, detail="users.db not found")

    conn = connect_archive(db_path)
    try:
        history_table = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='reputation_history'"
        ).fetchone()
        if not history_table:
            payload = {
                "entries": [], "total": 0, "offset": offset, "limit": limit
            }
        else:
            total = conn.execute(
                "SELECT COUNT(*) FROM reputation_history WHERE user_id = ?",
                (uid,),
            ).fetchone()[0]
            rows = conn.execute(
                """
                SELECT reputation_id, giver_user_id, giver_username,
                       giver_reputation, rating, rating_type, reason,
                       updated_at, post_url
                FROM reputation_history
                WHERE user_id = ?
                ORDER BY updated_sort DESC, reputation_id DESC
                LIMIT ? OFFSET ?
                """,
                (uid, limit, offset),
            ).fetchall()
            payload = {
                "entries": [dict(row) for row in rows],
                "total": total,
                "offset": offset,
                "limit": limit,
            }
    finally:
        conn.close()

    cache.set(cache_key, payload, CACHE_TTL_USER)
    return payload
