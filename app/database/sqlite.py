import sqlite3
import urllib.parse
from pathlib import Path

from app.config import DATA_DIR


def threads_db_path() -> Path:
    return DATA_DIR / "threads.db"


def users_db_path() -> Path:
    return DATA_DIR / "users.db"


def index_path(name: str) -> Path:
    return DATA_DIR / "index" / name


def connect_archive(path: Path | str) -> sqlite3.Connection:
    absolute = str(Path(path).resolve()).replace("\\", "/")
    uri_path = urllib.parse.quote(absolute, safe="/:")
    conn = sqlite3.connect(
        f"file:{uri_path}?mode=ro&immutable=1",
        uri=True,
    )
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    return conn
