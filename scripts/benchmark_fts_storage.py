"""Compare standalone and external-content FTS5 database sizes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time


ROOT_DIR = Path(__file__).resolve().parent.parent


FTS_POST_COLUMNS = (
    "post_description, author_username, author_id, thread_id, post_id"
)
FTS_THREAD_COLUMNS = (
    "title, author_username, author_id, categories, thread_id"
)


def copy_database(source: Path, destination: Path) -> None:
    destination.unlink(missing_ok=True)
    conn = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
    conn.execute("VACUUM INTO ?", (str(destination.resolve()),))
    conn.close()


def build_fts(path: Path, external: bool) -> float:
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE IF EXISTS fts_posts")
    conn.execute("DROP TABLE IF EXISTS fts_threads")

    if external:
        conn.execute(
            "CREATE VIRTUAL TABLE fts_posts USING fts5("
            f"{FTS_POST_COLUMNS}, content='posts', "
            "content_rowid='rowid', columnsize=0)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE fts_threads USING fts5("
            f"{FTS_THREAD_COLUMNS}, content='threads', "
            "content_rowid='rowid', columnsize=0)"
        )
        started = time.perf_counter()
        conn.execute("INSERT INTO fts_posts(fts_posts) VALUES('rebuild')")
        conn.execute("INSERT INTO fts_threads(fts_threads) VALUES('rebuild')")
    else:
        conn.execute(
            f"CREATE VIRTUAL TABLE fts_posts USING fts5({FTS_POST_COLUMNS})"
        )
        conn.execute(
            f"CREATE VIRTUAL TABLE fts_threads USING fts5({FTS_THREAD_COLUMNS})"
        )
        started = time.perf_counter()
        conn.execute(
            f"INSERT INTO fts_posts({FTS_POST_COLUMNS}) "
            f"SELECT {FTS_POST_COLUMNS} FROM posts"
        )
        conn.execute(
            f"INSERT INTO fts_threads({FTS_THREAD_COLUMNS}) "
            f"SELECT {FTS_THREAD_COLUMNS} FROM threads"
        )

    conn.execute("INSERT INTO fts_posts(fts_posts) VALUES('optimize')")
    conn.execute("INSERT INTO fts_threads(fts_threads) VALUES('optimize')")
    conn.commit()
    elapsed = time.perf_counter() - started
    conn.execute("VACUUM")
    conn.close()
    return elapsed


def search_manifest(path: Path) -> dict:
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    manifest = {}
    for term in ("the", "script", "roblox", "admin", "download"):
        manifest[f"posts:{term}"] = conn.execute(
            "SELECT COUNT(*) FROM fts_posts WHERE fts_posts MATCH ?",
            (f'"{term}"',),
        ).fetchone()[0]
        manifest[f"threads:{term}"] = conn.execute(
            "SELECT COUNT(*) FROM fts_threads WHERE fts_threads MATCH ?",
            (f'"{term}"',),
        ).fetchone()[0]
        post_rows = conn.execute(
            "SELECT p.post_id, p.thread_id, p.post_description "
            "FROM fts_posts f JOIN posts p ON p.post_id=f.post_id "
            "WHERE fts_posts MATCH ? ORDER BY p.post_id LIMIT 100",
            (f'"{term}"',),
        ).fetchall()
        thread_rows = conn.execute(
            "SELECT t.thread_id, t.title, t.categories "
            "FROM fts_threads f JOIN threads t ON t.thread_id=f.thread_id "
            "WHERE fts_threads MATCH ? ORDER BY t.thread_id LIMIT 100",
            (f'"{term}"',),
        ).fetchall()
        manifest[f"post_join:{term}"] = hashlib.sha256(
            json.dumps(post_rows, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        manifest[f"thread_join:{term}"] = hashlib.sha256(
            json.dumps(thread_rows, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
    manifest["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT_DIR / "benchmarks" / "thread_migration_zip_10000.db",
    )
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()

    standalone = args.source.with_name("fts_standalone.db")
    external = args.source.with_name("fts_external.db")
    copy_database(args.source, standalone)
    copy_database(args.source, external)

    standalone_seconds = build_fts(standalone, external=False)
    external_seconds = build_fts(external, external=True)
    standalone_manifest = search_manifest(standalone)
    external_manifest = search_manifest(external)
    if standalone_manifest != external_manifest:
        raise RuntimeError(
            f"FTS results differ: {standalone_manifest} != {external_manifest}"
        )

    print(
        f"base_bytes={args.source.stat().st_size} "
        f"standalone_bytes={standalone.stat().st_size} "
        f"external_bytes={external.stat().st_size} "
        f"standalone_seconds={standalone_seconds:.3f} "
        f"external_seconds={external_seconds:.3f} "
        f"external_ratio={external.stat().st_size/standalone.stat().st_size:.4f} "
        "search_output=identical"
    )

    if args.cleanup:
        standalone.unlink()
        external.unlink()
        print("temporary_files=removed")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
