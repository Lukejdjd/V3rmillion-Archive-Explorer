"""Post and thread search endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.cache import get_cache
from app.config import CACHE_TTL_SEARCH
from app.database.queries import (
    bounded_int,
    enrich_post_row,
    sanitize_fts_query,
)
from app.database.sqlite import connect_archive, threads_db_path

router = APIRouter(tags=["search"])

THREAD_PREFIXES = {
    "exploit": "[EXPLOIT]",
    "giveaway": "[GIVEAWAY]",
    "release": "[RELEASE]",
    "request": "[REQUEST]",
    "free": "[FREE]",
    "paid": "[PAID]",
}


@router.get("/search_posts")
def search_posts(
    q: str = Query(""),
    uid: str | None = None,
    thread_id: str | None = None,
    start_ts: str | None = None,
    end_ts: str | None = None,
    sort: str = Query("desc"),
    offset: int = Query(0),
    limit: int = Query(50),
    op_only: str = Query("0"),
) -> dict[str, Any]:
    q = (q or "").strip()
    uid_input = uid
    offset = bounded_int(offset, 0, 0, 100_000_000)
    limit = bounded_int(limit, 50, 1, 100)
    fetch_limit = limit + 1
    op_only_flag = op_only == "1"

    try:
        start_ts_i = int(start_ts) if start_ts not in (None, "") else None
    except Exception:
        start_ts_i = None
    try:
        end_ts_i = int(end_ts) if end_ts not in (None, "") else None
    except Exception:
        end_ts_i = None

    cache = get_cache()
    cache_key = cache.make_key(
        "search_posts",
        {
            "q": q,
            "uid": uid_input,
            "thread_id": thread_id,
            "start_ts": start_ts_i,
            "end_ts": end_ts_i,
            "sort": sort,
            "offset": offset,
            "limit": limit,
            "op_only": op_only_flag,
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
    cur.execute("SELECT sql FROM sqlite_master WHERE name='fts_posts'")
    fts_posts_schema = cur.fetchone()
    fts_posts_external = bool(
        fts_posts_schema and "content='posts'" in (fts_posts_schema[0] or "")
    )
    fts_posts_join = (
        "p.rowid = f.rowid" if fts_posts_external else "p.post_id = f.post_id"
    )

    select_fields = "p.*, t.title AS thread_title"
    left_join_user = ""

    def extra_filters():
        sql, params = "", []
        if thread_id:
            sql += " AND p.thread_id = ?"
            params.append(thread_id)
        if start_ts_i is not None:
            sql += " AND CAST(p.unix_time AS INTEGER) >= ?"
            params.append(start_ts_i)
        if end_ts_i is not None:
            sql += " AND CAST(p.unix_time AS INTEGER) <= ?"
            params.append(end_ts_i)
        if op_only_flag:
            sql += " AND p.is_op = 1"
        return sql, params

    def order_clause():
        if sort == "likes_desc":
            return (
                " ORDER BY CAST(COALESCE(NULLIF(p.likes, ''), '0') AS INTEGER) DESC, "
                "CAST(p.unix_time AS INTEGER) DESC"
            )
        if sort == "likes_asc":
            return (
                " ORDER BY CAST(COALESCE(NULLIF(p.likes, ''), '0') AS INTEGER) ASC, "
                "CAST(p.unix_time AS INTEGER) DESC"
            )
        if sort == "asc":
            return " ORDER BY CAST(p.unix_time AS INTEGER) ASC, p.post_number ASC"
        return " ORDER BY CAST(p.unix_time AS INTEGER) DESC, p.post_number DESC"

    extra_sql, extra_params = extra_filters()
    rows: list[dict] = []

    uid_is_numeric = uid_input and (
        str(uid_input).isdigit() or str(uid_input).startswith("pid_")
    )

    try:
        if q:
            sanitized_q = sanitize_fts_query(q)
            fts_where = "fts_posts MATCH ?"
            fts_params: list[Any] = [sanitized_q]

            if uid_input and not uid_is_numeric:
                fts_where = "fts_posts MATCH ? AND f.author_username = ?"
                fts_params = [sanitized_q, uid_input]
            elif uid_input and uid_is_numeric:
                extra_sql += " AND p.author_id = ?"
                extra_params.append(uid_input)

            try:
                sql = (
                    f"SELECT {select_fields} FROM fts_posts f "
                    f"JOIN posts p ON {fts_posts_join} "
                    "LEFT JOIN threads t ON t.thread_id = p.thread_id "
                    f"{left_join_user} WHERE {fts_where}"
                ) + extra_sql
                params = fts_params + extra_params
                if sort != "relevance":
                    sql += order_clause()
                sql += " LIMIT ? OFFSET ?"
                params.extend([fetch_limit, offset])
                cur.execute(sql, params)
                rows = [dict(r) for r in cur.fetchall()]
            except Exception as db_err:
                print(f"FTS search_posts failed ({db_err}), falling back to LIKE.")

            if not rows:
                fb_sql = (
                    f"SELECT {select_fields} FROM posts p "
                    f"LEFT JOIN threads t ON t.thread_id = p.thread_id "
                    f"{left_join_user} WHERE p.post_description LIKE ?"
                )
                fb_params: list[Any] = [f"%{q}%"]
                if uid_input and not uid_is_numeric:
                    fb_sql += " AND p.author_username = ?"
                    fb_params.append(uid_input)
                elif uid_input and uid_is_numeric:
                    fb_sql += " AND p.author_id = ?"
                    fb_params.append(uid_input)
                fb_sql += extra_sql
                fb_params.extend(extra_params)
                fb_sql += order_clause() + " LIMIT ? OFFSET ?"
                fb_params.extend([fetch_limit, offset])
                cur.execute(fb_sql, fb_params)
                rows = [dict(r) for r in cur.fetchall()]

        elif uid_input and not uid_is_numeric:
            try:
                sql = (
                    f"SELECT {select_fields} FROM fts_posts f "
                    f"JOIN posts p ON {fts_posts_join} "
                    "LEFT JOIN threads t ON t.thread_id = p.thread_id "
                    f"{left_join_user} WHERE f.author_username = ?"
                ) + extra_sql + order_clause() + " LIMIT ? OFFSET ?"
                params = [uid_input] + extra_params + [fetch_limit, offset]
                cur.execute(sql, params)
                rows = [dict(r) for r in cur.fetchall()]
            except Exception as db_err:
                print(f"FTS username lookup failed ({db_err}), falling back.")
                sql = (
                    f"SELECT {select_fields} FROM posts p "
                    f"LEFT JOIN threads t ON t.thread_id = p.thread_id "
                    f"{left_join_user} WHERE p.author_username = ?"
                ) + extra_sql + order_clause() + " LIMIT ? OFFSET ?"
                params = [uid_input] + extra_params + [fetch_limit, offset]
                cur.execute(sql, params)
                rows = [dict(r) for r in cur.fetchall()]

        else:
            if uid_input and uid_is_numeric:
                extra_sql = " AND p.author_id = ?" + extra_sql
                extra_params = [uid_input] + extra_params
            sql = (
                f"SELECT {select_fields} FROM posts p "
                f"LEFT JOIN threads t ON t.thread_id = p.thread_id "
                f"{left_join_user} WHERE 1=1"
            ) + extra_sql + order_clause() + " LIMIT ? OFFSET ?"
            params = extra_params + [fetch_limit, offset]
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    has_more = len(rows) > limit
    rows = rows[:limit]
    for row in rows:
        enrich_post_row(row)

    payload = {"results": rows, "has_more": has_more, "offset": offset, "limit": limit}
    cache.set(cache_key, payload, CACHE_TTL_SEARCH)
    return payload


@router.get("/search_threads")
def search_threads(
    q: str = Query(""),
    cat: str = Query(""),
    prefix: str = Query(""),
    author: str = Query(""),
    sort: str = Query("relevance"),
    order: str = Query("desc"),
    offset: int = Query(0),
    limit: int = Query(24),
) -> dict[str, Any]:
    import json

    q = (q or "").strip()
    cat = (cat or "").strip()
    prefix = (prefix or "").strip().lower()
    if prefix and prefix not in THREAD_PREFIXES:
        raise HTTPException(status_code=400, detail="invalid thread prefix")
    author = (author or "").strip()
    offset = bounded_int(offset, 0, 0, 100_000_000)
    limit = bounded_int(limit, 24, 1, 100)

    cache = get_cache()
    cache_key = cache.make_key(
        "search_threads",
        {
            "q": q,
            "cat": cat,
            "prefix": prefix,
            "author": author,
            "sort": sort,
            "order": order,
            "offset": offset,
            "limit": limit,
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

    cur.execute("SELECT sql FROM sqlite_master WHERE name='fts_threads'")
    fts_schema = cur.fetchone()
    has_fts = fts_schema is not None
    fts_join = (
        "t.rowid = f.rowid"
        if fts_schema and "content='threads'" in (fts_schema[0] or "")
        else "t.thread_id = f.thread_id"
    )

    dir_sql = "ASC" if order == "asc" else "DESC"
    if sort == "relevance" and not q:
        sort = "thread_id"

    params: list[Any] = []
    sort_by_likes = sort == "likes"

    try:
        if has_fts and q:
            sanitized_q = sanitize_fts_query(q)
            like_select = (
                "COALESCE(MAX(CAST(NULLIF(p.likes, '') AS INTEGER)), 0)"
                if sort_by_likes
                else "0"
            )
            base = (
                "SELECT t.thread_id, t.title, t.author_username, t.author_id, "
                "t.categories, t.date, t.reply_count, " + like_select + " AS like_count "
                f"FROM fts_threads f JOIN threads t ON {fts_join} "
            )
            if sort_by_likes:
                base += "LEFT JOIN posts p ON p.thread_id = t.thread_id "
            base += "WHERE fts_threads MATCH ?"
            params.append(sanitized_q)
        else:
            like_select = (
                "(SELECT COALESCE(MAX(CAST(NULLIF(p.likes, '') AS INTEGER)), 0) "
                "FROM posts p WHERE p.thread_id = threads.thread_id)"
                if sort_by_likes
                else "0"
            )
            base = (
                "SELECT thread_id, title, author_username, author_id, "
                "categories, date, reply_count, " + like_select + " AS like_count "
                "FROM threads WHERE 1=1"
            )

        if cat:
            base += " AND t.categories LIKE ?" if (has_fts and q) else " AND categories LIKE ?"
            params.append(f"%{cat}%")

        if prefix:
            title_col = "t.title" if (has_fts and q) else "title"
            base += f" AND {title_col} LIKE ? COLLATE NOCASE"
            params.append(f"{THREAD_PREFIXES[prefix]}%")

        if author:
            col = "f.author_username" if (has_fts and q) else "author_username"
            base += f" AND {col} = ?"
            params.append(author)

        if has_fts and q and sort_by_likes:
            base += " GROUP BY t.thread_id"

        count_base = base

        if sort == "relevance" and q and has_fts:
            pass
        elif sort == "thread_id":
            base += f" ORDER BY CAST({'t.' if (has_fts and q) else ''}thread_id AS INTEGER) {dir_sql}"
        elif sort == "title":
            base += f" ORDER BY {'t.' if (has_fts and q) else ''}title {dir_sql}"
        elif sort == "date":
            base += f" ORDER BY {'t.' if (has_fts and q) else ''}date {dir_sql}"
        elif sort == "replies":
            base += f" ORDER BY {'t.' if (has_fts and q) else ''}reply_count {dir_sql}"
        elif sort == "likes":
            base += f" ORDER BY like_count {dir_sql}"

        count_sql = f"SELECT COUNT(*) FROM ({count_base})"
        try:
            cur.execute(count_sql, params)
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

        like_counts: dict[str, Any] = {}
        if rows and not sort_by_likes:
            thread_ids = [str(r["thread_id"]) for r in rows]
            placeholders = ",".join("?" for _ in thread_ids)
            cur.execute(
                "SELECT thread_id, COALESCE(MAX(CAST(NULLIF(likes, '') AS INTEGER)), 0) "
                f"FROM posts WHERE thread_id IN ({placeholders}) GROUP BY thread_id",
                thread_ids,
            )
            like_counts = {str(row[0]): row[1] for row in cur.fetchall()}

        out = []
        for r in rows:
            try:
                cats = json.loads(r["categories"]) if r["categories"] else []
            except Exception:
                cats = []
            out.append(
                {
                    "thread_id": str(r["thread_id"]),
                    "title": r["title"] or "Untitled Thread",
                    "author": r["author_username"] or "",
                    "author_id": r["author_id"] or "",
                    "categories": cats,
                    "date": r["date"] or "",
                    "reply_count": r["reply_count"],
                    "like_count": like_counts.get(str(r["thread_id"]), r["like_count"]),
                }
            )
    finally:
        conn.close()

    payload = {"results": out, "total": total, "offset": offset, "limit": limit}
    cache.set(cache_key, payload, CACHE_TTL_SEARCH)
    return payload


@router.get("/titles")
def titles() -> dict[str, Any]:
    cache = get_cache()
    cache_key = "titles:count"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    db_path = threads_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=500, detail="threads.db not found")

    conn = connect_archive(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as cnt FROM threads")
        row = cur.fetchone()
    finally:
        conn.close()

    payload = {"thread_count": row["cnt"] if row else 0}
    cache.set(cache_key, payload, CACHE_TTL_SEARCH)
    return payload
