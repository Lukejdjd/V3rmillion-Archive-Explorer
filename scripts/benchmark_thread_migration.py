"""Benchmark the current end-to-end thread-to-SQLite migration safely.

The benchmark writes only to a disposable database under ``benchmarks`` and
never touches ``data/threads.db``.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import zipfile

from benchmark_thread_parser import CORPUS
import thread_parser


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT_DIR / "benchmarks" / "thread_migration_benchmark.db"


def migrate_one(args: tuple[str, str]) -> object:
    thread_id, db_path = args
    thread_parser.DB_PATH = db_path
    return thread_parser.run_migration_on_file(thread_id, overwrite=True)


def table_hash(conn: sqlite3.Connection, table: str, key: str) -> str:
    digest = hashlib.sha256()
    cursor = conn.execute(f"SELECT * FROM {table} ORDER BY {key}")
    for row in cursor:
        digest.update(
            json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            .encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--small-threads", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument(
        "--pipeline",
        choices=("legacy", "batched", "zip-serial"),
        default="legacy",
    )
    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers must be at least 1")

    thread_ids = CORPUS
    if args.small_threads:
        source = sqlite3.connect(
            f"file:{thread_parser.DB_PATH}?mode=ro",
            uri=True,
        )
        thread_ids = [
            row[0]
            for row in source.execute(
                "SELECT thread_id FROM threads "
                "WHERE reply_count BETWEEN 0 AND 10 "
                "ORDER BY thread_id LIMIT ? OFFSET ?",
                (args.small_threads, args.offset),
            )
        ]
        source.close()

    args.database.parent.mkdir(parents=True, exist_ok=True)
    for path in (
        args.database,
        Path(str(args.database) + "-shm"),
        Path(str(args.database) + "-wal"),
    ):
        path.unlink(missing_ok=True)

    started = time.perf_counter()
    counts = {"done": 0, "skipped": 0, "failed": 0}
    errors: list[str] = []

    if args.pipeline == "legacy":
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    migrate_one,
                    (thread_id, str(args.database)),
                ): thread_id
                for thread_id in thread_ids
            }
            for future in as_completed(futures):
                result = future.result()
                if result is True:
                    counts["done"] += 1
                elif result == "skipped":
                    counts["skipped"] += 1
                else:
                    counts["failed"] += 1
                    errors.append(str(result))
    elif args.pipeline == "batched":
        thread_parser.DB_PATH = str(args.database)
        write_conn = thread_parser.init_db(create_fts=False)
        write_conn.execute("DROP TABLE IF EXISTS fts_posts")
        write_conn.execute("DROP TABLE IF EXISTS fts_threads")
        write_conn.commit()
        write_cur = write_conn.cursor()
        pending_threads = []
        pending_posts = []

        def flush_pending() -> None:
            if not pending_threads:
                return
            thread_parser.write_thread_batch(
                write_cur,
                pending_threads,
                pending_posts,
            )
            write_conn.commit()
            pending_threads.clear()
            pending_posts.clear()

        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    thread_parser.parse_thread_worker,
                    thread_id,
                ): thread_id
                for thread_id in thread_ids
            }
            for future in as_completed(futures):
                status, payload = future.result()
                if status == "success":
                    thread_row, post_rows = payload
                    pending_threads.append(thread_row)
                    pending_posts.extend(post_rows)
                    counts["done"] += 1
                    if len(pending_threads) >= 500:
                        flush_pending()
                else:
                    counts["failed"] += 1
                    errors.append(str(payload))

        flush_pending()
        write_conn.close()
    else:
        thread_parser.DB_PATH = str(args.database)
        write_conn = thread_parser.init_db(create_fts=False)
        write_cur = write_conn.cursor()
        pending_threads = []
        pending_posts = []

        archive = zipfile.ZipFile(ROOT_DIR / "data" / "threads.zip")
        wanted = set(thread_ids)
        entries = {thread_id: [] for thread_id in thread_ids}
        for info in archive.infolist():
            if info.is_dir():
                continue
            parts = info.filename.split("/")
            if (
                len(parts) >= 3
                and parts[0] == "threads"
                and parts[1] in wanted
            ):
                entries[parts[1]].append(info)

        for thread_id in thread_ids:
            pages = {}
            metadata = None
            for info in entries[thread_id]:
                name = info.filename.rsplit("/", 1)[-1]
                content = archive.read(info).decode("utf-8")
                if name.endswith(".html"):
                    pages[name] = content
                elif name == "thread.json":
                    metadata = content

            try:
                thread_row, post_rows = (
                    thread_parser.build_thread_rows_from_pages(
                        thread_id,
                        pages,
                        metadata,
                    )
                )
                pending_threads.append(thread_row)
                pending_posts.extend(post_rows)
                counts["done"] += 1

                if len(pending_threads) >= 500:
                    thread_parser.write_thread_batch(
                        write_cur,
                        pending_threads,
                        pending_posts,
                    )
                    write_conn.commit()
                    pending_threads.clear()
                    pending_posts.clear()
            except Exception as exc:
                counts["failed"] += 1
                errors.append(f"Error {thread_id}: {exc}")

        thread_parser.write_thread_batch(
            write_cur,
            pending_threads,
            pending_posts,
        )
        write_conn.commit()
        write_conn.close()
        archive.close()

    elapsed = time.perf_counter() - started
    conn = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
    manifest = {
        "threads": conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0],
        "posts": conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
        "threads_sha256": table_hash(conn, "threads", "thread_id"),
        "posts_sha256": table_hash(conn, "posts", "post_id"),
    }
    conn.close()

    if args.manifest:
        if args.manifest.exists():
            expected = json.loads(args.manifest.read_text(encoding="utf-8"))
            if manifest != expected:
                print("DATABASE OUTPUT MISMATCH")
                print(json.dumps({"expected": expected, "actual": manifest}, indent=2))
                return 1
            print("database_output=identical")
        else:
            args.manifest.write_text(
                json.dumps(manifest, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"manifest={args.manifest}")

    print(
        f"pipeline={args.pipeline} workers={args.workers} "
        f"elapsed={elapsed:.3f}s "
        f"done={counts['done']} skipped={counts['skipped']} "
        f"failed={counts['failed']} threads={manifest['threads']} "
        f"posts={manifest['posts']}"
    )
    for error in errors[:10]:
        print(error)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
