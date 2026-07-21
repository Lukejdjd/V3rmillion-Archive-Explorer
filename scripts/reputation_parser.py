"""Add archived reputation history to an existing users.db.

This intentionally reuses the already validated profile rows and reads only
reputation_*.html entries directly from users.zip in archive order.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import zipfile

from tqdm import tqdm

from profile_parser import (
    REPUTATION_FILENAME_RE,
    parse_reputation_content,
    write_reputation_batch_to_db,
)


sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(ROOT_DIR, "data", "users.db")
DEFAULT_ZIP_PATH = os.path.join(ROOT_DIR, "data", "users.zip")
ARCHIVE_PATH_RE = re.compile(
    r"^users/(\d+)/(reputation_(\d+)\.html)$", re.IGNORECASE
)


def create_reputation_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reputation_history (
            reputation_id INTEGER PRIMARY KEY,
            user_id TEXT NOT NULL,
            giver_user_id TEXT,
            giver_username TEXT,
            giver_reputation TEXT,
            rating INTEGER,
            rating_type TEXT,
            reason TEXT,
            updated_at TEXT,
            updated_sort INTEGER,
            post_url TEXT,
            source_page INTEGER NOT NULL
        )
    """)


def run(db_path: str, zip_path: str, limit: int | None = None) -> None:
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"Existing users database not found: {db_path}")
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f"User archive not found: {zip_path}")

    print("Loading reputation-page index from users.zip...")
    archive = zipfile.ZipFile(zip_path)
    reputation_entries = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        match = ARCHIVE_PATH_RE.fullmatch(info.filename.replace("\\", "/"))
        if not match or not REPUTATION_FILENAME_RE.fullmatch(match.group(2)):
            continue
        reputation_entries.append((info, match.group(1), int(match.group(3))))
        if limit is not None and len(reputation_entries) >= max(0, limit):
            break

    print(f"Found {len(reputation_entries):,} reputation pages.")

    conn = sqlite3.connect(db_path, timeout=300)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA locking_mode=EXCLUSIVE")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-262144")
    create_reputation_table(conn)
    conn.execute("DROP INDEX IF EXISTS idx_reputation_user_updated")
    conn.execute("DELETE FROM reputation_history")
    conn.commit()

    pending = []
    parsed_records = 0
    failed_pages = 0

    def flush() -> None:
        if not pending:
            return
        write_reputation_batch_to_db(conn, pending)
        conn.commit()
        pending.clear()

    with tqdm(
        total=len(reputation_entries),
        desc="Reputation pages",
        unit="page",
        dynamic_ncols=True,
        mininterval=0.2,
    ) as progress:
        for info, user_id, page_number in reputation_entries:
            try:
                content = archive.read(info).decode("utf-8", errors="replace")
                rows = parse_reputation_content(content, user_id, page_number)
                pending.extend(rows)
                parsed_records += len(rows)
                if len(pending) >= 50000:
                    flush()
            except Exception as exc:
                failed_pages += 1
                progress.write(f"Failed {info.filename}: {exc}")
            progress.update(1)

    flush()
    archive.close()

    print("Creating reputation lookup index...")
    conn.execute("""
        CREATE INDEX idx_reputation_user_updated
        ON reputation_history(user_id, updated_sort DESC, reputation_id DESC)
    """)
    conn.commit()
    print("Running VACUUM...")
    conn.execute("VACUUM")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()

    print("=" * 70)
    print(f"Reputation pages parsed: {len(reputation_entries) - failed_pages:,}")
    print(f"Reputation pages failed: {failed_pages:,}")
    print(f"Reputation records: {parsed_records:,}")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--zip-path", default=DEFAULT_ZIP_PATH)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    run(
        os.path.abspath(args.db_path),
        os.path.abspath(args.zip_path),
        args.limit,
    )
