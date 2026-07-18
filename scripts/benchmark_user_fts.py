"""Compare user FTS storage, output, and API-style query latency."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
import time

import profile_parser


ROOT_DIR = Path(__file__).resolve().parent.parent
TERMS = ("admin", "roblox", "script", "banned", "lucas")


def copy_database(source: Path, destination: Path) -> None:
    destination.unlink(missing_ok=True)
    conn = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
    conn.execute("VACUUM INTO ?", (str(destination.resolve()),))
    conn.close()


def timed(conn, sql: str, term: str, repeats: int = 7) -> float:
    values = []
    parameter = f'"{term}"'
    for _ in range(repeats):
        started = time.perf_counter()
        conn.execute(sql, (parameter,)).fetchall()
        values.append((time.perf_counter() - started) * 1000)
    return statistics.median(values)


def manifest(conn, join: str) -> dict:
    result = {}
    for term in TERMS:
        parameter = f'"{term}"'
        result[f"count:{term}"] = conn.execute(
            "SELECT COUNT(*) FROM fts_users WHERE fts_users MATCH ?",
            (parameter,),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT u.user_id,u.username,u.user_title,u.user_rank,"
            "u.past_usernames FROM fts_users f "
            f"JOIN users u ON {join} WHERE fts_users MATCH ? "
            "ORDER BY CAST(u.user_id AS INTEGER) LIMIT 100",
            (parameter,),
        ).fetchall()
        result[f"rows:{term}"] = hashlib.sha256(
            json.dumps(rows, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
    return result


def timings(conn, join: str) -> dict:
    count_sql = (
        "SELECT COUNT(*) FROM fts_users f "
        f"JOIN users u ON {join} WHERE fts_users MATCH ?"
    )
    page_sql = (
        "SELECT u.user_id,u.username,u.user_title,u.user_rank,"
        "u.reputation,u.post_count,u.thread_count,u.user_stars,"
        "u.user_group,u.joined,u.awards,u.past_usernames "
        "FROM fts_users f "
        f"JOIN users u ON {join} WHERE fts_users MATCH ? LIMIT 24"
    )
    return {
        term: {
            "count_ms": round(timed(conn, count_sql, term), 3),
            "page_ms": round(timed(conn, page_sql, term), 3),
        }
        for term in TERMS
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", type=Path, default=ROOT_DIR / "data" / "users.db"
    )
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    output_dir = ROOT_DIR / "benchmarks"
    output_dir.mkdir(exist_ok=True)
    standalone = output_dir / "users_fts_standalone.db"
    external = output_dir / "users_fts_external.db"
    copy_database(args.source, standalone)
    copy_database(args.source, external)

    old = sqlite3.connect(standalone)
    old_manifest = manifest(old, "u.user_id=f.user_id")
    old_timings = timings(old, "u.user_id=f.user_id")

    new = sqlite3.connect(external)
    new.execute("DROP TABLE fts_users")
    new.execute("ALTER TABLE users ADD COLUMN past_usernames_search TEXT")
    new.execute("""
        UPDATE users SET past_usernames_search = (
            SELECT GROUP_CONCAT(json_extract(value, '$.username'), ' ')
            FROM json_each(COALESCE(past_usernames, '[]'))
        )
    """)
    profile_parser.create_external_user_fts(new)
    started = time.perf_counter()
    new.execute("INSERT INTO fts_users(fts_users) VALUES('rebuild')")
    new.execute("INSERT INTO fts_users(fts_users) VALUES('optimize')")
    new.commit()
    rebuild_seconds = time.perf_counter() - started
    new.execute("VACUUM")
    new_manifest = manifest(new, "u.rowid=f.rowid")
    new_text_timings = timings(new, "u.user_id=f.user_id")
    new_rowid_timings = timings(new, "u.rowid=f.rowid")
    integrity = new.execute("PRAGMA integrity_check").fetchone()[0]

    if old_manifest != new_manifest:
        raise RuntimeError("Standalone and external FTS search output differs")
    if integrity != "ok":
        raise RuntimeError(f"Integrity check failed: {integrity}")

    print(json.dumps({
        "users": new.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "standalone_bytes": standalone.stat().st_size,
        "external_bytes": external.stat().st_size,
        "external_ratio": round(external.stat().st_size / standalone.stat().st_size, 4),
        "external_rebuild_seconds": round(rebuild_seconds, 3),
        "standalone_text_join": old_timings,
        "external_text_join": new_text_timings,
        "external_rowid_join": new_rowid_timings,
        "search_output": "identical",
        "integrity": integrity,
    }, indent=2))
    old.close()
    new.close()

    if args.cleanup:
        standalone.unlink()
        external.unlink()
        print("temporary_files=removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
