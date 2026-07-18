import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from thread_parser import run_migration_on_file, PARSED_DIR

thread_id = "1218438"

os.makedirs(PARSED_DIR, exist_ok=True)

file_name = f"parsed_thread_{thread_id}.json"

print(f"🚀 Running manual repair on thread: {thread_id}...")
print(f"   parsed dir : {PARSED_DIR}")

start = time.perf_counter()

result = run_migration_on_file(
    file_name,
    PARSED_DIR,
)

elapsed = time.perf_counter() - start

print(f"Result: {result}")
print(f"⏱ Took {elapsed:.3f} seconds")