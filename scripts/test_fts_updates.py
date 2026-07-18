"""Verify external-content FTS remains consistent after thread UPSERTs."""

from __future__ import annotations

from pathlib import Path
import sqlite3

import thread_parser


ROOT_DIR = Path(__file__).resolve().parent.parent
TEST_DB = ROOT_DIR / "benchmarks" / "fts_update_test.db"
THREAD_IDS = ("1000006", "1218438")
TERMS = ("the", "site", "script", "closing")


def search_manifest(conn: sqlite3.Connection) -> dict:
    result = {}
    for table in ("fts_posts", "fts_threads"):
        for term in TERMS:
            result[f"{table}:{term}"] = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {table} MATCH ?",
                (f'"{term}"',),
            ).fetchone()[0]
    return result


def main() -> int:
    TEST_DB.unlink(missing_ok=True)
    Path(str(TEST_DB) + "-shm").unlink(missing_ok=True)
    Path(str(TEST_DB) + "-wal").unlink(missing_ok=True)
    thread_parser.DB_PATH = str(TEST_DB)

    for _ in range(2):
        for thread_id in THREAD_IDS:
            result = thread_parser.run_migration_on_file(
                thread_id,
                overwrite=True,
            )
            if result is not True:
                raise RuntimeError(result)

    conn = sqlite3.connect(TEST_DB)
    before = search_manifest(conn)
    conn.execute("INSERT INTO fts_posts(fts_posts) VALUES('rebuild')")
    conn.execute("INSERT INTO fts_threads(fts_threads) VALUES('rebuild')")
    after = search_manifest(conn)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()

    if before != after:
        raise RuntimeError(f"FTS changed after rebuild: {before} != {after}")
    if integrity != "ok":
        raise RuntimeError(f"Integrity check failed: {integrity}")

    TEST_DB.unlink()
    print("fts_updates=consistent integrity=ok temporary_files=removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
