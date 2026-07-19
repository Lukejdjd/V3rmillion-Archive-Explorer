#!/usr/bin/env python3
"""Create helpful B-tree indexes on archive databases before packaging.

Run this after parsers finish and BEFORE enabling immutable read-only mounts
or packaging .zst release artifacts. Live production DBs are opened with
mode=ro&immutable=1 and cannot be altered at runtime.

Usage:
  python scripts/optimize_indexes.py
  python scripts/optimize_indexes.py --data-dir ./data
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


INDEXES = {
    "threads.db": [
        ("idx_posts_thread_id", "CREATE INDEX IF NOT EXISTS idx_posts_thread_id ON posts(thread_id)"),
        ("idx_posts_author_id", "CREATE INDEX IF NOT EXISTS idx_posts_author_id ON posts(author_id)"),
        ("idx_posts_unix_time", "CREATE INDEX IF NOT EXISTS idx_posts_unix_time ON posts(unix_time)"),
        ("idx_posts_thread_time", "CREATE INDEX IF NOT EXISTS idx_posts_thread_time ON posts(thread_id, unix_time)"),
        ("idx_threads_author_id", "CREATE INDEX IF NOT EXISTS idx_threads_author_id ON threads(author_id)"),
        ("idx_threads_date", "CREATE INDEX IF NOT EXISTS idx_threads_date ON threads(date)"),
    ],
    "users.db": [
        ("idx_users_username", "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)"),
        ("idx_users_joined", "CREATE INDEX IF NOT EXISTS idx_users_joined ON users(joined)"),
    ],
}


def optimize_db(path: Path) -> None:
    if not path.exists():
        print(f"skip missing {path}")
        return

    print(f"optimizing {path} ...")
    conn = sqlite3.connect(str(path))
    try:
        cur = conn.cursor()
        for name, ddl in INDEXES.get(path.name, []):
            print(f"  {name}")
            cur.execute(ddl)
        conn.commit()
        print("  ANALYZE")
        cur.execute("ANALYZE")
        conn.commit()
        print(f"  integrity: {cur.execute('PRAGMA integrity_check').fetchone()[0]}")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("ARCHIVE_DATA_DIR", "data"),
        help="Directory containing threads.db and users.db",
    )
    args = parser.parse_args()
    data_dir = Path(args.data_dir)

    optimize_db(data_dir / "threads.db")
    optimize_db(data_dir / "users.db")
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
