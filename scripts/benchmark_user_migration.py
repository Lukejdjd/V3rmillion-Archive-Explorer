"""Benchmark direct users.zip parsing into a disposable SQLite database."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import time

import profile_parser


ROOT_DIR = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", type=int, default=10000)
    parser.add_argument("--offset", type=int, default=100000)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()

    output = ROOT_DIR / "benchmarks" / "user_migration_zip.db"
    output.unlink(missing_ok=True)
    Path(str(output) + "-shm").unlink(missing_ok=True)
    Path(str(output) + "-wal").unlink(missing_ok=True)
    profile_parser.DB_PATH = str(output)
    conn = profile_parser.init_users_db(create_fts=False)
    cur = conn.cursor()

    started = time.perf_counter()
    archive, entries = profile_parser.load_user_archive_index()
    index_seconds = time.perf_counter() - started
    user_ids = list(entries)[args.offset:args.offset + args.users]

    started = time.perf_counter()
    pending = []
    failures = 0
    for user_id in user_ids:
        try:
            row, _ = profile_parser.build_user_row_from_archive(
                archive, user_id, entries[user_id]
            )
            pending.append(row)
            if len(pending) >= 500:
                profile_parser.write_user_batch_to_db(cur, pending)
                conn.commit()
                pending.clear()
        except Exception:
            failures += 1
    if pending:
        profile_parser.write_user_batch_to_db(cur, pending)
        conn.commit()
    parse_seconds = time.perf_counter() - started
    archive.close()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()

    print(
        f"users={count} failures={failures} "
        f"archive_index_seconds={index_seconds:.3f} "
        f"parse_write_seconds={parse_seconds:.3f} "
        f"total_seconds={index_seconds + parse_seconds:.3f} "
        f"integrity={integrity}"
    )
    if args.cleanup:
        output.unlink()
        print("temporary_files=removed")
    return 0 if count + failures == len(user_ids) and integrity == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
