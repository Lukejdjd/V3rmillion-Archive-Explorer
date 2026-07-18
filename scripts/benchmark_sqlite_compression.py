"""Create and compress a disposable compact SQLite snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time


ROOT_DIR = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT_DIR / "data" / "threads.db",
    )
    parser.add_argument(
        "--compact",
        type=Path,
        default=ROOT_DIR / "benchmarks" / "threads_compact_test.db",
    )
    parser.add_argument("--level", type=int, default=3)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()

    zstd = shutil.which("zstd")
    if not zstd:
        parser.error("zstd executable not found")

    compressed = Path(str(args.compact) + ".zst")
    args.compact.parent.mkdir(parents=True, exist_ok=True)
    args.compact.unlink(missing_ok=True)
    compressed.unlink(missing_ok=True)

    source_bytes = args.source.stat().st_size
    started = time.perf_counter()
    source = sqlite3.connect(
        f"file:{args.source.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    source.execute("VACUUM INTO ?", (str(args.compact.resolve()),))
    source.close()
    vacuum_seconds = time.perf_counter() - started

    compact = sqlite3.connect(
        f"file:{args.compact.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    integrity = compact.execute("PRAGMA integrity_check").fetchone()[0]
    compact.close()
    if integrity != "ok":
        raise RuntimeError(f"Compact database integrity check failed: {integrity}")

    started = time.perf_counter()
    subprocess.run(
        [
            zstd,
            f"-{args.level}",
            "-T0",
            "-f",
            str(args.compact),
            "-o",
            str(compressed),
        ],
        check=True,
    )
    compression_seconds = time.perf_counter() - started
    subprocess.run([zstd, "-t", str(compressed)], check=True)

    compact_bytes = args.compact.stat().st_size
    compressed_bytes = compressed.stat().st_size
    print(
        f"source_bytes={source_bytes} compact_bytes={compact_bytes} "
        f"zstd_bytes={compressed_bytes} "
        f"vacuum_seconds={vacuum_seconds:.2f} "
        f"compression_seconds={compression_seconds:.2f} "
        f"compact_ratio={compact_bytes/source_bytes:.4f} "
        f"zstd_ratio={compressed_bytes/source_bytes:.4f}"
    )

    if args.cleanup:
        args.compact.unlink()
        compressed.unlink()
        print("temporary_files=removed")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
