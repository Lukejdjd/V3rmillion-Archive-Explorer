# Usage:
#   python scripts/profile_parser.py                     # all users (resume mode)
#   python scripts/profile_parser.py --overwrite         # force reparse all
#   python scripts/profile_parser.py --user 123456       # single user
#   python scripts/profile_parser.py --user 123456 --overwrite

import json
import os
import re
import sys
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from selectolax.parser import HTMLParser, Node
from tqdm import tqdm
import sqlite3

from v3rm_assets import (
    normalize_image_path,
    resolve_award,
    resolve_usergroup,
)

# Keep progress/status output valid when Windows redirects stdout.
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
USERS_DIR = os.path.join(DATA_DIR, "users")
USERS_ZIP = os.path.join(DATA_DIR, "users.zip")
INDEX_DIR = os.path.join(DATA_DIR, "index")
DB_PATH = os.path.join(DATA_DIR, "users.db")
DEFAULT_FOLDER_WORKERS = min(24, os.cpu_count() or 1)
USER_ARCHIVE_FILENAMES = {
    "profile.html", "alts.html", "past_usernames.html"
}


def parse_awards_from_profile(tree: HTMLParser) -> List[Dict[str, str]]:
    awards: List[Dict[str, str]] = []
    
    # 1. Process award tables
    for table in tree.css("table.tborder"):
        header = table.css_first(".thead")
        if not header or "award" not in header.text(strip=True).lower():
            continue
        for link in table.css('a[href*="awards.php"]'):
            img = link.css_first("img")
            if not img:
                continue
            src = img.attributes.get("src", "").strip()
            title = (img.attributes.get("alt") or link.attributes.get("title") or "").strip()
            awards.append(resolve_award(src, title))

    # 2. Process sidebars/header award icons
    for link in tree.css('.author_statistics a[href*="awards.php"], .profile_header a[href*="awards.php"]'):
        img = link.css_first("img")
        if not img:
            continue
        src = img.attributes.get("src", "").strip()
        title = (img.attributes.get("alt") or link.attributes.get("title") or "").strip()
        resolved = resolve_award(src, title)
        if resolved not in awards:
            awards.append(resolved)
            
    return awards


def extract_table_value(tree: HTMLParser, label: str) -> Optional[str]:
    for row in tree.css("tr"):
        strong = row.css_first("td strong")
        if not strong:
            continue
        if strong.text(strip=True).rstrip(":").lower() == label.lower():
            cells = row.css("td")
            if len(cells) >= 2:
                return cells[1].text(separator=" ", strip=True)
    return None


def parse_alts_content(content: Optional[str]) -> List[Dict[str, str]]:
    alts: List[Dict[str, str]] = []
    if content is None:
        return alts
    tree = HTMLParser(content)

    for table in tree.css("table.tborder"):
        header = table.css_first(".thead")
        if not header or "alternate" not in header.text(strip=True).lower():
            continue

        for row in table.css("tr"):
            cells = row.css("td")
            if len(cells) != 3:
                continue
            if cells[0].css_first(".smalltext"):
                continue

            user_cell = cells[0]
            link = user_cell.css_first("a[href*='user_id=']")
            if not link:
                continue

            user_id_match = re.search(r"user_id=(\d+)", link.attributes.get("href", ""))
            if not user_id_match:
                continue
            alt_user_id = user_id_match.group(1)

            strong = user_cell.css_first("strong")
            alt_username = strong.text(strip=True) if strong else (link.text(strip=True) or "Deleted user")

            certainty = cells[1].css_first(".box")
            certainty_text = certainty.text(strip=True) if certainty else None
            last_matched = cells[2].text(strip=True) or None

            alts.append({
                "user_id": alt_user_id,
                "username": alt_username,
                "certainty": certainty_text,
                "last_matched": last_matched,
            })
    return alts


def parse_alts_html(alts_path: str) -> List[Dict[str, str]]:
    if not os.path.exists(alts_path):
        return []
    with open(alts_path, "r", encoding="utf-8") as f:
        return parse_alts_content(f.read())


def parse_past_usernames_content(
    content: Optional[str],
) -> List[Dict[str, str]]:
    past: List[Dict[str, str]] = []
    if content is None:
        return past
    tree = HTMLParser(content)

    for table in tree.css("table.tborder"):
        header = table.css_first(".thead")
        if not header or "username history" not in header.text(strip=True).lower():
            continue

        for row in table.css("tr"):
            cells = row.css("td")
            if len(cells) < 2:
                continue
            
            first_cell_class = cells[0].attributes.get("class", "")
            if first_cell_class and "tcat" in first_cell_class:
                continue
                
            username = cells[0].text(strip=True)
            date_changed = cells[1].text(strip=True)
            if username:
                past.append({
                    "username": username,
                    "date_changed": date_changed or None,
                })

    return past


def parse_past_usernames_html(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return parse_past_usernames_content(f.read())


def parse_profile_content(
    content: str,
    user_id: str,
    alts_content: Optional[str] = None,
    past_usernames_content: Optional[str] = None,
) -> Dict[str, Any]:
    tree = HTMLParser(content)

    title_node = tree.css_first("head title")
    title_match = re.search(
        r"Profile of (.+?)(?:\s*-|\s*$)",
        title_node.text() if title_node else ""
    )
    username = title_match.group(1).strip() if title_match else None

    header = tree.css_first("fieldset.profile_header")
    user_title = None
    group_image = None
    user_stars = 0

    if header:
        name_el = header.css_first(".largetext strong")
        if name_el:
            inner = name_el.css_first("[style]")
            username = (inner.text(strip=True) if inner else name_el.text(strip=True)) or username

        smalltext = header.css_first(".smalltext")
        if smalltext:
            smalltext_raw = smalltext.text(separator="\n", strip=True) or ""
            for line in smalltext_raw.split("\n"):
                line_str = line.strip()
                if line_str.startswith("(") and line_str.endswith(")"):
                    user_title = line_str.strip("()")
                    break

            user_stars = len(smalltext.css('img[src*="star"]'))

            userbar = smalltext.css_first(".userbar-image, img[src*='UserBars']")
            if userbar:
                group_image = userbar.attributes.get("src")

    if not username:
        username = f"User {user_id}"

    joined = extract_table_value(tree, "Joined") or extract_table_value(tree, "Joined:")
    last_visit = extract_table_value(tree, "Last Visit")
    posts_raw = extract_table_value(tree, "Total Posts")
    threads_raw = extract_table_value(tree, "Total Threads")
    time_online = extract_table_value(tree, "Time Spent Online")

    reputation = None
    rep_el = tree.css_first(".reputation_positive, .reputation_negative, .reputation_neutral")
    if rep_el:
        reputation = rep_el.text(strip=True)

    awards = parse_awards_from_profile(tree)

    user_rank = None
    userbar_el = tree.css_first(".userbar-image")
    if userbar_el:
        user_rank = userbar_el.attributes.get("alt") or userbar_el.attributes.get("title")
        group_image = group_image or userbar_el.attributes.get("src")

    user_group = resolve_usergroup(
        group_image,
        user_rank,
    )

    alts = parse_alts_content(alts_content)
    past_usernames = parse_past_usernames_content(past_usernames_content)

    return {
        "user_id": user_id,
        "username": username,
        "user_title": user_title,
        "user_rank": user_rank,
        "user_stars": user_stars,
        "user_group": user_group,
        "joined": joined,
        "last_visit": last_visit,
        "post_count": posts_raw.split("(")[0].strip() if posts_raw else None,
        "thread_count": threads_raw.split("(")[0].strip() if threads_raw else None,
        "time_online": time_online,
        "reputation": reputation,
        "awards": awards,
        "alts": alts,
        "past_usernames": past_usernames,
    }


def parse_profile_html(html_path: str, user_id: str) -> Dict[str, Any]:
    folder = os.path.dirname(html_path)
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

    def read_optional(name: str) -> Optional[str]:
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    return parse_profile_content(
        content,
        user_id,
        read_optional("alts.html"),
        read_optional("past_usernames.html"),
    )


def init_users_db(create_fts: bool = True):
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=100000")
    conn.execute("PRAGMA temp_store=MEMORY")

    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            user_title TEXT,
            user_rank TEXT,
            user_group TEXT,
            user_stars INTEGER,
            reputation TEXT,
            post_count TEXT,
            thread_count TEXT,
            joined TEXT,
            last_visit TEXT,
            time_online TEXT,
            awards TEXT,
            alts TEXT,
            past_usernames TEXT,
            past_usernames_search TEXT
        );
    ''')

    try:
        conn.execute("ALTER TABLE users ADD COLUMN past_usernames TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN past_usernames_search TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    if create_fts:
        try:
            create_external_user_fts(conn)
            if user_fts_is_external(conn):
                create_user_fts_triggers(conn)
        except sqlite3.OperationalError:
            pass
    return conn


def user_fts_is_external(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='fts_users'"
    ).fetchone()
    return bool(row and "content='users'" in row[0])


def create_external_user_fts(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS fts_users USING fts5(
            user_id, username, user_title, user_rank, past_usernames_search,
            content='users', content_rowid='rowid', columnsize=0
        )
    """)


def drop_user_fts_triggers(conn: sqlite3.Connection) -> None:
    for name in ("users_fts_ai", "users_fts_ad", "users_fts_au"):
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")


def create_user_fts_triggers(conn: sqlite3.Connection) -> None:
    drop_user_fts_triggers(conn)
    conn.executescript("""
        CREATE TRIGGER users_fts_ai AFTER INSERT ON users BEGIN
            INSERT INTO fts_users(
                rowid, user_id, username, user_title, user_rank,
                past_usernames_search
            ) VALUES (
                new.rowid, new.user_id, new.username, new.user_title,
                new.user_rank, new.past_usernames_search
            );
        END;

        CREATE TRIGGER users_fts_ad AFTER DELETE ON users BEGIN
            INSERT INTO fts_users(
                fts_users, rowid, user_id, username, user_title, user_rank,
                past_usernames_search
            ) VALUES (
                'delete', old.rowid, old.user_id, old.username,
                old.user_title, old.user_rank, old.past_usernames_search
            );
        END;

        CREATE TRIGGER users_fts_au AFTER UPDATE ON users BEGIN
            INSERT INTO fts_users(
                fts_users, rowid, user_id, username, user_title, user_rank,
                past_usernames_search
            ) VALUES (
                'delete', old.rowid, old.user_id, old.username,
                old.user_title, old.user_rank, old.past_usernames_search
            );
            INSERT INTO fts_users(
                rowid, user_id, username, user_title, user_rank,
                past_usernames_search
            ) VALUES (
                new.rowid, new.user_id, new.username, new.user_title,
                new.user_rank, new.past_usernames_search
            );
        END;
    """)


def profile_to_row(user_id: str, profile: Dict[str, Any]) -> Tuple:
    ug = profile.get("user_group")
    if isinstance(ug, dict):
        ug = {key: value for key, value in ug.items() if key != "image"}

    past_usernames = profile.get("past_usernames")
    past_usernames_search = " ".join(
        item["username"]
        for item in (past_usernames or [])
        if item.get("username")
    ) or None

    return (
        user_id, profile.get("username"), profile.get("user_title"),
        profile.get("user_rank"),
        json.dumps(ug) if ug is not None else None,
        profile.get("user_stars"), profile.get("reputation"),
        profile.get("post_count"), profile.get("thread_count"),
        profile.get("joined"), profile.get("last_visit"),
        profile.get("time_online"),
        json.dumps(profile.get("awards"))
        if profile.get("awards") is not None else None,
        json.dumps(profile.get("alts"))
        if profile.get("alts") is not None else None,
        json.dumps(past_usernames) if past_usernames is not None else None,
        past_usernames_search,
    )


# ── STEP A: Pure parallel worker (NO SQLITE WRITING) ───────────
def parse_user_worker(user_id: str) -> Tuple[str, Any]:
    """Worker Job: Only parses text files via selectolax into raw structured tuples."""
    html_folder = os.path.join(USERS_DIR, user_id)
    profile_html = os.path.join(html_folder, "profile.html")

    if not os.path.exists(profile_html):
        return "missing", f"Skipping {user_id}: profile.html missing"

    try:
        profile = parse_profile_html(profile_html, user_id)
        return "success", (profile_to_row(user_id, profile), profile)
    except Exception as exc:
        return "error", f"Error parsing {user_id}: {exc}"


# ── STEP B: Direct linear single-threaded DB flush ──────────────
def write_user_batch_to_db(cur, batch_rows: List[Tuple]):
    cur.executemany("""
        INSERT INTO users
        (user_id, username, user_title, user_rank, user_group, user_stars, reputation, post_count, thread_count, 
         joined, last_visit, time_online, awards, alts, past_usernames,
         past_usernames_search)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            user_title=excluded.user_title,
            user_rank=excluded.user_rank,
            user_group=excluded.user_group,
            user_stars=excluded.user_stars,
            reputation=excluded.reputation,
            post_count=excluded.post_count,
            thread_count=excluded.thread_count,
            joined=excluded.joined,
            last_visit=excluded.last_visit,
            time_online=excluded.time_online,
            awards=excluded.awards,
            alts=excluded.alts,
            past_usernames=excluded.past_usernames,
            past_usernames_search=excluded.past_usernames_search
    """, batch_rows)


def run_migration_on_user(user_id: str, overwrite: bool = False) -> Any:
    """Legacy single file processing fallback."""
    if not overwrite:
        conn = init_users_db()
        exists = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
        conn.close()
        if exists:
            return "skipped"

    status, payload = parse_user_worker(user_id)
    if status == "success":
        row, profile = payload
        conn = init_users_db()
        cur = conn.cursor()
        write_user_batch_to_db(cur, [row])
        
        fts_exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fts_users'").fetchone()
        if fts_exists and not user_fts_is_external(conn):
            update_fts_for_user(conn, user_id, profile)
            
        conn.commit()
        conn.close()
        return True
    return payload


def update_fts_for_user(conn: sqlite3.Connection, user_id: str, profile: Dict[str, Any]):
    past = profile.get('past_usernames') or []
    past_usernames_text = ' '.join(p['username'] for p in past if p.get('username'))

    conn.execute("DELETE FROM fts_users WHERE user_id = ?", (user_id,))
    conn.execute("""INSERT INTO fts_users(user_id, username, user_title, user_rank, past_usernames)
                    VALUES (?, ?, ?, ?, ?)""",
                 (user_id, profile.get('username'), profile.get('user_title'),
                  profile.get('user_rank'), past_usernames_text or None))


def load_user_archive_index():
    archive = zipfile.ZipFile(USERS_ZIP)
    entries = {}
    for info in archive.infolist():
        parts = info.filename.split("/")
        if (
            info.is_dir()
            or len(parts) != 3
            or parts[0] != "users"
            or parts[2] not in USER_ARCHIVE_FILENAMES
        ):
            continue
        entries.setdefault(parts[1], {})[parts[2]] = info

    entries = {
        user_id: user_entries
        for user_id, user_entries in entries.items()
        if "profile.html" in user_entries
    }
    return archive, entries


def build_user_row_from_archive(archive, user_id, entries):
    def read_optional(name: str) -> Optional[str]:
        info = entries.get(name)
        return archive.read(info).decode("utf-8") if info else None

    profile = parse_profile_content(
        read_optional("profile.html"),
        user_id,
        read_optional("alts.html"),
        read_optional("past_usernames.html"),
    )
    return profile_to_row(user_id, profile), profile


def run_concurrent_migration(
    overwrite: bool = False,
    workers: int = DEFAULT_FOLDER_WORKERS,
    source: str = "auto",
):
    if source == "auto":
        source = "zip" if os.path.isfile(USERS_ZIP) else "folders"

    archive = None
    archive_entries = None
    if source == "zip":
        if not os.path.isfile(USERS_ZIP):
            raise FileNotFoundError(f"User archive not found: {USERS_ZIP}")
        print("Loading user ZIP directory...")
        archive, archive_entries = load_user_archive_index()
        user_ids = list(archive_entries)
    elif source == "folders":
        if not os.path.isdir(USERS_DIR):
            raise FileNotFoundError(f"No users directory at {USERS_DIR}")
        user_ids = [
            entry.name for entry in os.scandir(USERS_DIR) if entry.is_dir()
        ]
    else:
        raise ValueError(f"Unknown user source: {source}")

    total = len(user_ids)

    if total == 0:
        print("No user folders found.")
        return

    if source == "zip":
        print(f"Found {total:,} users in ZIP. Starting sequential parse...")
    else:
        print(
            f"Found {total:,} user profiles. "
            f"Starting migration with {workers} workers..."
        )

    conn = init_users_db(create_fts=False)
    if not overwrite:
        print("Scoping DB to skip processed users...")
        existing_ids = {r[0] for r in conn.execute("SELECT user_id FROM users").fetchall()}
        users_to_process = [uid for uid in user_ids if uid not in existing_ids]
        skipped_count = len(user_ids) - len(users_to_process)
        print(f"Skipping {skipped_count:,} already matched profiles.")
    else:
        users_to_process = user_ids
        skipped_count = 0
    if not users_to_process:
        if user_fts_is_external(conn):
            conn.close()
            if archive:
                archive.close()
            print("No users need processing.")
            return
        print("No user rows need parsing; upgrading the existing FTS index.")

    print("Dropping FTS index for bulk insert...")
    drop_user_fts_triggers(conn)
    conn.execute("DROP TABLE IF EXISTS fts_users")
    conn.execute("DROP VIEW IF EXISTS users_fts_content")
    conn.commit()
    conn.close()

    chunk_size = 10000
    counts = {"done": 0, "skipped": skipped_count, "failed": 0}

    # Open one writer and commit bounded batches.
    write_conn = sqlite3.connect(DB_PATH, timeout=300)
    write_conn.execute("PRAGMA journal_mode=WAL")
    write_conn.execute("PRAGMA synchronous=OFF")
    write_cur = write_conn.cursor()
    pending_rows = []

    def flush_pending():
        if not pending_rows:
            return
        write_user_batch_to_db(write_cur, pending_rows)
        write_conn.commit()
        pending_rows.clear()

    def handle_result(status, res, pbar):
        if status == "success":
            pending_rows.append(res[0])
            counts["done"] += 1
            if len(pending_rows) >= 500:
                flush_pending()
        elif status == "missing":
            counts["skipped"] += 1
        else:
            counts["failed"] += 1
            print(f"\n{res}")
        pbar.update(1)

    with tqdm(total=total, desc="Profiles", unit="user", dynamic_ncols=True, mininterval=0.3) as pbar:
        if skipped_count > 0:
            pbar.update(skipped_count)

        if source == "zip":
            assert archive is not None and archive_entries is not None
            for user_id in users_to_process:
                try:
                    payload = build_user_row_from_archive(
                        archive, user_id, archive_entries[user_id]
                    )
                    handle_result("success", payload, pbar)
                except Exception as exc:
                    handle_result(
                        "error", f"Error parsing {user_id}: {exc}", pbar
                    )
        else:
            with ProcessPoolExecutor(max_workers=workers) as executor:
                for i in range(0, len(users_to_process), chunk_size):
                    chunk = users_to_process[i:i + chunk_size]
                    futures = {
                        executor.submit(parse_user_worker, uid): uid
                        for uid in chunk
                    }
                    for future in as_completed(futures):
                        handle_result(*future.result(), pbar)

    flush_pending()
    write_conn.close()
    if archive:
        archive.close()

    print("\n" + "="*70)
    print(f"✅ Done:     {counts['done']:,}")
    print(f"⏭ Skipped:  {counts['skipped']:,}")
    print(f"❌ Failed:   {counts['failed']:,}")
    print("="*70)

    os.makedirs(INDEX_DIR, exist_ok=True)
    print(f"🎉 Migration completed! Indexed {counts['done']:,} users.")

    # ── Rebuild FTS in one step ───────────────────────────────────────────
    print("\nRebuilding FTS index...")
    conn = sqlite3.connect(DB_PATH, timeout=300)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    print("  Preparing past-username search text...")
    conn.execute("""
        UPDATE users
        SET past_usernames_search = (
            SELECT GROUP_CONCAT(json_extract(value, '$.username'), ' ')
            FROM json_each(COALESCE(past_usernames, '[]'))
        )
        WHERE past_usernames_search IS NULL
          AND past_usernames IS NOT NULL
    """)
    create_external_user_fts(conn)
    print("  Populating fts_users...")
    conn.execute("INSERT INTO fts_users(fts_users) VALUES('rebuild')")
    conn.execute("INSERT INTO fts_users(fts_users) VALUES('optimize')")
    create_user_fts_triggers(conn)
    conn.commit()
    print("FTS index rebuilt.")

    print("Running VACUUM (this may take a moment)...")
    conn.execute("VACUUM")
    conn.close()
    print("VACUUM complete.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", type=str, default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--workers", type=int, default=DEFAULT_FOLDER_WORKERS)
    parser.add_argument(
        "--source",
        choices=("auto", "zip", "folders"),
        default="auto",
        help="Bulk input source; auto prefers data/users.zip",
    )
    args = parser.parse_args()

    if args.user:
        result = run_migration_on_user(args.user, args.overwrite)
        print(result)
    else:
        run_concurrent_migration(
            overwrite=args.overwrite,
            workers=args.workers,
            source=args.source,
        )
