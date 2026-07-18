"""Verify external-content user FTS remains consistent after UPSERTs."""

from __future__ import annotations

from pathlib import Path
import sqlite3

import profile_parser


ROOT_DIR = Path(__file__).resolve().parent.parent
TEST_DB = ROOT_DIR / "benchmarks" / "user_fts_update_test.db"
USER_IDS = ("1", "1000000")
TERMS = ("admin", "roblox", "script", "banned")


def search_manifest(conn: sqlite3.Connection) -> dict:
    return {
        term: conn.execute(
            "SELECT COUNT(*) FROM fts_users WHERE fts_users MATCH ?",
            (f'"{term}"',),
        ).fetchone()[0]
        for term in TERMS
    }


def main() -> int:
    TEST_DB.unlink(missing_ok=True)
    Path(str(TEST_DB) + "-shm").unlink(missing_ok=True)
    Path(str(TEST_DB) + "-wal").unlink(missing_ok=True)
    profile_parser.DB_PATH = str(TEST_DB)

    for _ in range(2):
        for user_id in USER_IDS:
            result = profile_parser.run_migration_on_user(
                user_id, overwrite=True
            )
            if result is not True:
                raise RuntimeError(result)

    conn = sqlite3.connect(TEST_DB)
    before = search_manifest(conn)
    conn.execute("INSERT INTO fts_users(fts_users) VALUES('rebuild')")
    after = search_manifest(conn)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()

    if before != after:
        raise RuntimeError(f"FTS changed after rebuild: {before} != {after}")
    if integrity != "ok" or user_count != len(USER_IDS):
        raise RuntimeError(
            f"integrity={integrity} users={user_count}"
        )
    TEST_DB.unlink()
    print("user_fts_updates=consistent integrity=ok temporary_files=removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
