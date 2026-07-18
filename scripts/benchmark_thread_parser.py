"""Benchmark thread parsing and detect any output changes.

This intentionally benchmarks only ``thread_parser``.  It does not write to
SQLite, rebuild FTS indexes, or modify archive data.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

import thread_parser


CORPUS = [
    # 0-2 replies
    "1000006", "1000010", "1000014", "1000018", "1000022", "1000023",
    "100002", "1000031", "1000030", "1000021", "1000033", "1000036",
    # 3-10 replies
    "100000", "1000011", "1000017", "1000016", "1000015", "1000024",
    "100003", "1000032", "1000034", "1000039", "100004", "1000043",
    # 11-50 replies
    "10000", "1000013", "1000019", "1000012", "100005", "1000053",
    "100009", "100011", "1000134", "1000154", "1000164", "100017",
    # 51-250 replies
    "100012", "1000237", "1000285", "1000312", "1000360", "1000430",
    "100065", "1000660", "1000990", "1001054", "1001391", "1001358",
    # 251+ replies
    "1001510", "1022286", "1034793", "1035271", "1036865", "1037564",
    "1038908", "1041652", "10425", "1043302", "1057338", "1058658",
    # Known complex, long thread used during initial profiling.
    "1218438",
]


def stable_hash(value: Any) -> str:
    if isinstance(value, dict) and "thread_path" in value:
        value = dict(value)
        value["thread_path"] = os.path.basename(value["thread_path"])
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def count_posts(post: dict[str, Any] | None) -> int:
    if not post:
        return 0
    return 1 + sum(count_posts(reply) for reply in post.get("replies", []))


def parse_one(thread_id: str) -> tuple[str, dict[str, Any], float]:
    cpu_start = time.process_time()
    folder = os.path.join(thread_parser.THREADS_DIR, thread_id)
    parsed = thread_parser.thread_parser(folder)
    result = {
        "sha256": stable_hash(parsed),
        "pages": sum(name.endswith(".html") for name in os.listdir(folder)),
        "posts": count_posts(parsed.get("thread_content")),
    }
    return thread_id, result, time.process_time() - cpu_start


def run_once(
    thread_ids: list[str],
    workers: int,
) -> tuple[dict[str, Any], float, float]:
    wall_start = time.perf_counter()
    cpu_start = time.process_time()

    if workers == 1:
        parsed_results = map(parse_one, thread_ids)
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            parsed_results = executor.map(parse_one, thread_ids, chunksize=1)

    results: dict[str, Any] = {}
    worker_cpu = 0.0
    for thread_id, result, task_cpu in parsed_results:
        results[thread_id] = result
        worker_cpu += task_cpu

    return (
        results,
        time.perf_counter() - wall_start,
        worker_cpu if workers > 1 else time.process_time() - cpu_start,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", type=Path)
    args = parser.parse_args()

    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    wall_times: list[float] = []
    cpu_times: list[float] = []
    first_results: dict[str, Any] | None = None

    for run_number in range(1, args.runs + 1):
        results, wall, cpu = run_once(CORPUS, args.workers)
        if first_results is None:
            first_results = results
        elif results != first_results:
            raise RuntimeError("Parser output changed between benchmark runs")
        wall_times.append(wall)
        cpu_times.append(cpu)
        print(f"run={run_number} wall={wall:.3f}s cpu={cpu:.3f}s")

    assert first_results is not None
    manifest = {
        "thread_ids": CORPUS,
        "results": first_results,
    }

    if args.baseline:
        expected = json.loads(args.baseline.read_text(encoding="utf-8"))
        if manifest != expected:
            changed = [
                thread_id
                for thread_id in CORPUS
                if manifest["results"].get(thread_id)
                != expected.get("results", {}).get(thread_id)
            ]
            print(f"OUTPUT MISMATCH: {changed}")
            return 1
        print("output=identical")

    if args.write_baseline:
        args.write_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.write_baseline.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"baseline={args.write_baseline}")

    print(
        f"threads={len(CORPUS)} "
        f"workers={args.workers} "
        f"pages={sum(item['pages'] for item in first_results.values())} "
        f"posts={sum(item['posts'] for item in first_results.values())} "
        f"median_wall={statistics.median(wall_times):.3f}s "
        f"median_cpu={statistics.median(cpu_times):.3f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
