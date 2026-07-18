"""Benchmark API-style post and thread FTS queries and join strategies."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import statistics
import time


ROOT_DIR = Path(__file__).resolve().parent.parent
TERMS = ("the", "script", "roblox", "admin", "download")


def median_ms(conn, sql: str, term: str, repeats: int = 9) -> float:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        conn.execute(sql, (f'"{term}"',)).fetchall()
        samples.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(samples), 3)


def benchmark(conn, join_kind: str) -> dict:
    post_join = (
        "p.rowid=f.rowid" if join_kind == "rowid" else "p.post_id=f.post_id"
    )
    thread_join = (
        "t.rowid=f.rowid"
        if join_kind == "rowid"
        else "t.thread_id=f.thread_id"
    )
    post_page = (
        "SELECT p.*,t.title AS thread_title FROM fts_posts f "
        f"JOIN posts p ON {post_join} "
        "LEFT JOIN threads t ON t.thread_id=p.thread_id "
        "WHERE fts_posts MATCH ? LIMIT 50"
    )
    thread_base = (
        "SELECT t.thread_id,t.title,t.author_username,t.author_id,"
        "t.categories,t.date,t.reply_count,"
        "COALESCE(MAX(CAST(NULLIF(p.likes,'') AS INTEGER)),0) AS like_count "
        "FROM fts_threads f "
        f"JOIN threads t ON {thread_join} "
        "LEFT JOIN posts p ON p.thread_id=t.thread_id "
        "WHERE fts_threads MATCH ? GROUP BY t.thread_id"
    )
    thread_count = f"SELECT COUNT(*) FROM ({thread_base})"
    thread_page = thread_base + " LIMIT 24"
    return {
        term: {
            "post_page_ms": median_ms(conn, post_page, term),
            "thread_count_ms": median_ms(conn, thread_count, term),
            "thread_page_ms": median_ms(conn, thread_page, term),
        }
        for term in TERMS
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT_DIR / "benchmarks" / "thread_migration_zip_10000.db",
    )
    args = parser.parse_args()
    conn = sqlite3.connect(
        f"file:{args.source.resolve().as_posix()}?mode=ro", uri=True
    )
    output = {
        "threads": conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0],
        "posts": conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
        "text_join": benchmark(conn, "text"),
        "rowid_join": benchmark(conn, "rowid"),
    }
    print(json.dumps(output, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
