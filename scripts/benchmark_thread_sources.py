"""Compare cold extracted-file reads with sequential reads from threads.zip."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import time
import zipfile


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"


def read_folder(thread_id: str) -> tuple[int, int]:
    files = 0
    size = 0
    for path in (DATA_DIR / "threads" / thread_id).iterdir():
        if path.suffix in {".html", ".json"}:
            size += len(path.read_bytes())
            files += 1
    return files, size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", type=int, default=2000)
    parser.add_argument("--offset", type=int, default=300000)
    parser.add_argument("--workers", type=int, default=24)
    args = parser.parse_args()

    conn = sqlite3.connect(
        f"file:{(DATA_DIR / 'threads.db').as_posix()}?mode=ro",
        uri=True,
    )
    thread_ids = [
        row[0]
        for row in conn.execute(
            "SELECT thread_id FROM threads "
            "WHERE reply_count BETWEEN 0 AND 10 "
            "ORDER BY thread_id LIMIT ? OFFSET ?",
            (args.threads, args.offset),
        )
    ]
    conn.close()

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        folder_results = list(executor.map(read_folder, thread_ids))
    folder_seconds = time.perf_counter() - started
    folder_files = sum(item[0] for item in folder_results)
    folder_bytes = sum(item[1] for item in folder_results)

    wanted = set(thread_ids)
    started = time.perf_counter()
    archive = zipfile.ZipFile(DATA_DIR / "threads.zip")
    archive_files = [
        info
        for info in archive.infolist()
        if not info.is_dir()
        and len(parts := info.filename.split("/")) >= 3
        and parts[0] == "threads"
        and parts[1] in wanted
        and (parts[-1].endswith(".html") or parts[-1] == "thread.json")
    ]
    archive_files.sort(key=lambda info: info.header_offset)
    index_seconds = time.perf_counter() - started

    started = time.perf_counter()
    archive_bytes = 0
    for info in archive_files:
        archive_bytes += len(archive.read(info))
    archive_seconds = time.perf_counter() - started
    archive.close()

    print(
        f"threads={len(thread_ids)} workers={args.workers} "
        f"folder_files={folder_files} folder_bytes={folder_bytes} "
        f"folder_seconds={folder_seconds:.3f} "
        f"archive_files={len(archive_files)} archive_bytes={archive_bytes} "
        f"archive_index_seconds={index_seconds:.3f} "
        f"archive_read_seconds={archive_seconds:.3f}"
    )
    return 0 if (folder_files, folder_bytes) == (
        len(archive_files),
        archive_bytes,
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
