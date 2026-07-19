"""Shared SQL helpers and query builders used by API routers."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def parse_awards_field(val: Any) -> list:
    if val is None or val == "":
        return []
    if isinstance(val, (list, tuple)):
        return list(val)
    try:
        parsed = json.loads(val)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def parse_json_field(val: Any) -> Any:
    if val is None or val == "":
        return None
    try:
        return json.loads(val)
    except Exception:
        return None


def bounded_int(value: Any, default: int, minimum: int = 0, maximum: int = 1000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def sanitize_fts_query(q: str) -> str:
    return '"' + q.replace('"', '""') + '"'


def sanitize_fts_prefix_query(q: str) -> str:
    """Build an FTS5 prefix query so partial tokens match (e.g. moo -> moo*)."""
    cleaned = []
    for ch in q.strip():
        if ch.isalnum() or ch in {"_", "-", "."}:
            cleaned.append(ch)
        elif ch.isspace():
            cleaned.append(" ")
    tokens = [t for t in "".join(cleaned).split() if t]
    if not tokens:
        return ""
    # Prefix each token; AND them together for multi-word input.
    return " ".join(f'"{t}"*' for t in tokens)


def fts_join_clause(cur: sqlite3.Cursor, table: str, content_table: str, id_col: str) -> tuple[bool, str]:
    cur.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,))
    row = cur.fetchone()
    if not row:
        return False, ""
    sql = row[0] or ""
    external = f"content='{content_table}'" in sql
    join = (
        f"{content_table[0]}.rowid = f.rowid"
        if external
        else f"{content_table[0]}.{id_col} = f.{id_col}"
    )
    # Prefer explicit aliases used by callers
    if content_table == "posts":
        join = "p.rowid = f.rowid" if external else "p.post_id = f.post_id"
    elif content_table == "threads":
        join = "t.rowid = f.rowid" if external else "t.thread_id = f.thread_id"
    elif content_table == "users":
        join = "u.rowid = f.rowid" if external else "u.user_id = f.user_id"
    return True, join


def enrich_post_row(row: dict) -> dict:
    row["awards"] = parse_awards_field(row.get("awards"))
    if "user_group" in row:
        row["user_group"] = parse_json_field(row.get("user_group"))
    return row
