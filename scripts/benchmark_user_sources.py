"""Compare extracted user-file reads with sequential users.zip reads."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
import zipfile


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
WANTED_NAMES = {"profile.html", "alts.html", "past_usernames.html"}


def read_folder(user_id: str) -> tuple[int, int]:
    files = 0
    size = 0
    folder = DATA_DIR / "users" / user_id
    for name in WANTED_NAMES:
        path = folder / name
        if path.is_file():
            size += len(path.read_bytes())
            files += 1
    return files, size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", type=int, default=10000)
    parser.add_argument("--offset", type=int, default=100000)
    parser.add_argument("--workers", type=int, default=24)
    args = parser.parse_args()

    started = time.perf_counter()
    archive = zipfile.ZipFile(DATA_DIR / "users.zip")
    matching = []
    user_ids = []
    seen = set()
    start = args.offset
    stop = start + args.users
    profile_number = 0

    for info in archive.infolist():
        parts = info.filename.split("/")
        if (
            info.is_dir()
            or len(parts) != 3
            or parts[0] != "users"
            or parts[2] not in WANTED_NAMES
        ):
            continue
        user_id = parts[1]
        if parts[2] == "profile.html" and user_id not in seen:
            if start <= profile_number < stop:
                user_ids.append(user_id)
                seen.add(user_id)
            profile_number += 1

    wanted = set(user_ids)
    for info in archive.infolist():
        parts = info.filename.split("/")
        if (
            not info.is_dir()
            and len(parts) == 3
            and parts[0] == "users"
            and parts[1] in wanted
            and parts[2] in WANTED_NAMES
        ):
            matching.append(info)
    matching.sort(key=lambda item: item.header_offset)
    index_seconds = time.perf_counter() - started

    started = time.perf_counter()
    archive_bytes = sum(len(archive.read(info)) for info in matching)
    archive_seconds = time.perf_counter() - started
    archive.close()

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        folder_results = list(executor.map(read_folder, user_ids))
    folder_seconds = time.perf_counter() - started
    folder_files = sum(item[0] for item in folder_results)
    folder_bytes = sum(item[1] for item in folder_results)

    print(
        f"users={len(user_ids)} workers={args.workers} "
        f"folder_files={folder_files} folder_bytes={folder_bytes} "
        f"folder_seconds={folder_seconds:.3f} "
        f"archive_files={len(matching)} archive_bytes={archive_bytes} "
        f"archive_index_seconds={index_seconds:.3f} "
        f"archive_read_seconds={archive_seconds:.3f}"
    )
    return 0 if (folder_files, folder_bytes) == (
        len(matching), archive_bytes
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
