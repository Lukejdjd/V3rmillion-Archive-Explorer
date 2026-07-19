"""Entry point: run the FastAPI archive API with Uvicorn."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading

from app.config import HOST, MANAGE_IFRAMELY, PORT, WORKERS


def start_iframely() -> None:
    print("Starting Iframely...")
    subprocess.run(["docker", "compose", "up", "-d", "iframely"], check=True)
    print("Iframely running on http://localhost:8061")


def stop_iframely() -> None:
    print("\nStopping Iframely...")
    subprocess.run(["docker", "compose", "down", "iframely"])


def run() -> None:
    import uvicorn

    if MANAGE_IFRAMELY:
        start_iframely()

    print(f"Archive API running on http://{HOST}:{PORT}/static/search.html")
    print(f"   Uvicorn workers={WORKERS}  (set WEB_CONCURRENCY to change)")
    print("   Press Ctrl+C to stop everything\n")

    stop_event = threading.Event()

    def shutdown(sig, frame):
        if MANAGE_IFRAMELY:
            stop_iframely()
        print("Bye")
        stop_event.set()
        # Force uvicorn exit when launched programmatically
        os._exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Multiple workers: use import string so each worker loads the app.
    # Single worker can use the app object directly (better for local reload).
    if WORKERS > 1:
        uvicorn.run(
            "app.main:app",
            host=HOST,
            port=PORT,
            workers=WORKERS,
            proxy_headers=True,
            forwarded_allow_ips="*",
            log_level="info",
        )
    else:
        uvicorn.run(
            "app.main:app",
            host=HOST,
            port=PORT,
            workers=1,
            proxy_headers=True,
            forwarded_allow_ips="*",
            log_level="info",
        )


if __name__ == "__main__":
    run()
