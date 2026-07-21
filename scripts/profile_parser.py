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
from datetime import datetime
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
REPUTATION_FILENAME_RE = re.compile(r"^reputation_(\d+)\.html$", re.IGNORECASE)
REPUTATION_LABEL_RE = re.compile(
    r"^(Positive|Neutral|Negative)\s*\(([+-]?\d+)\):?",
    re.IGNORECASE,
)
REPUTATION_DATE_RE = re.compile(r"Last updated\s+(.+)$", re.IGNORECASE)


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
            link = user_cell.css_first("a[href*='uid='], a[href*='user_id=']")
            if not link:
                continue

            user_id_match = re.search(
                r"(?:^|[?&])(?:uid|user_id)=(\d+)",
                link.attributes.get("href", ""),
            )
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


def repair_alts_only(source: str = "auto") -> None:
    """Reparse only alts.html and update the existing users table in place."""
    if source == "auto":
        source = "zip" if os.path.isfile(USERS_ZIP) else "folders"

    conn = sqlite3.connect(DB_PATH, timeout=300)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    pending: List[Tuple[str, str]] = []
    processed = 0
    users_with_alts = 0
    updated_rows = 0

    def queue_update(user_id: str, alts: List[Dict[str, str]]) -> None:
        nonlocal processed, users_with_alts, updated_rows
        processed += 1
        if alts:
            users_with_alts += 1
        pending.append((json.dumps(alts, ensure_ascii=False), user_id))
        if len(pending) >= 1000:
            before = conn.total_changes
            conn.executemany("UPDATE users SET alts = ? WHERE user_id = ?", pending)
            updated_rows += conn.total_changes - before
            conn.commit()
            pending.clear()

    if source == "zip":
        if not os.path.isfile(USERS_ZIP):
            raise FileNotFoundError(f"User archive not found: {USERS_ZIP}")
        with zipfile.ZipFile(USERS_ZIP) as archive:
            infos = [
                info for info in archive.infolist()
                if not info.is_dir() and info.filename.endswith("/alts.html")
            ]
            with tqdm(infos, desc="User alts", unit="user", dynamic_ncols=True) as bar:
                for info in bar:
                    parts = info.filename.split("/")
                    if len(parts) != 3:
                        continue
                    content = archive.read(info).decode("utf-8", errors="replace")
                    queue_update(parts[1], parse_alts_content(content))
                    bar.set_postfix(found=users_with_alts, refresh=False)
    elif source == "folders":
        if not os.path.isdir(USERS_DIR):
            raise FileNotFoundError(f"No users directory at {USERS_DIR}")
        entries = [entry for entry in os.scandir(USERS_DIR) if entry.is_dir()]
        with tqdm(entries, desc="User alts", unit="user", dynamic_ncols=True) as bar:
            for entry in bar:
                alts_path = os.path.join(entry.path, "alts.html")
                if not os.path.isfile(alts_path):
                    continue
                queue_update(entry.name, parse_alts_html(alts_path))
                bar.set_postfix(found=users_with_alts, refresh=False)
    else:
        raise ValueError(f"Unknown user source: {source}")

    if pending:
        before = conn.total_changes
        conn.executemany("UPDATE users SET alts = ? WHERE user_id = ?", pending)
        updated_rows += conn.total_changes - before
        conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    print(
        f"Alt repair complete: parsed {processed:,} files, "
        f"found {users_with_alts:,} users with alts, updated {updated_rows:,} DB rows."
    )


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


def reputation_date_sort_value(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value.strip(), "%m-%d-%Y, %I:%M %p")
    except ValueError:
        return None
    return int(parsed.strftime("%Y%m%d%H%M"))


def parse_reputation_content(
    content: Optional[str],
    user_id: str,
    source_page: int,
) -> List[Tuple]:
    if not content:
        return []

    tree = HTMLParser(content)
    records: List[Tuple] = []
    for cell in tree.css('td[id^="rid"]'):
        reputation_id_match = re.fullmatch(
            r"rid(\d+)", cell.attributes.get("id", ""), re.IGNORECASE
        )
        if not reputation_id_match:
            continue

        reputation_id = int(reputation_id_match.group(1))
        giver_link = cell.css_first('a[href*="member.php?action=profile"]')
        giver_user_id = None
        giver_username = None
        if giver_link:
            giver_match = re.search(
                r"(?:[?&](?:uid|user_id)=)(\d+)",
                giver_link.attributes.get("href", ""),
                re.IGNORECASE,
            )
            giver_user_id = giver_match.group(1) if giver_match else None
            giver_username = giver_link.text(strip=True) or None

        giver_reputation = None
        smalltext = cell.css_first("span.smalltext")
        if smalltext:
            giver_rep_node = smalltext.css_first('a[href*="reputation.php"] strong')
            if giver_rep_node:
                giver_reputation = giver_rep_node.text(strip=True) or None

        rating_type = None
        rating_value = None
        for strong in cell.css("strong"):
            label_match = REPUTATION_LABEL_RE.match(strong.text(strip=True))
            if label_match:
                rating_type = label_match.group(1).lower()
                rating_value = int(label_match.group(2))
                break

        updated_at = None
        if smalltext:
            date_match = REPUTATION_DATE_RE.search(
                smalltext.text(separator=" ", strip=True)
            )
            if date_match:
                updated_at = date_match.group(1).strip()

        reason_node = cell.css_first('div[style*="word-break"]')
        reason = (
            reason_node.text(separator="\n", strip=True)
            if reason_node
            else None
        )

        post_url = None
        post_link = cell.css_first(
            'a[href*="showthread.php"], a[href*="post.php"], a[href*="#pid"]'
        )
        if post_link:
            post_url = post_link.attributes.get("href") or None

        records.append((
            reputation_id,
            user_id,
            giver_user_id,
            giver_username,
            giver_reputation,
            rating_value,
            rating_type,
            reason,
            updated_at,
            reputation_date_sort_value(updated_at),
            post_url,
            source_page,
        ))
    return records


def parse_reputation_folder(folder: str, user_id: str) -> List[Tuple]:
    pages = []
    for entry in os.scandir(folder):
        if not entry.is_file():
            continue
        match = REPUTATION_FILENAME_RE.fullmatch(entry.name)
        if match:
            pages.append((int(match.group(1)), entry.path))

    records: List[Tuple] = []
    for page_number, path in sorted(pages):
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            records.extend(
                parse_reputation_content(handle.read(), user_id, page_number)
            )
    return records


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

        CREATE TABLE IF NOT EXISTS reputation_history (
            reputation_id INTEGER PRIMARY KEY,
            user_id TEXT NOT NULL,
            giver_user_id TEXT,
            giver_username TEXT,
            giver_reputation TEXT,
            rating INTEGER,
            rating_type TEXT,
            reason TEXT,
            updated_at TEXT,
            updated_sort INTEGER,
            post_url TEXT,
            source_page INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_reputation_user_updated
        ON reputation_history(user_id, updated_sort DESC, reputation_id DESC);
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

        CREATE TRIGGER users_fts_au AFTER UPDATE OF
            user_id, username, user_title, user_rank, past_usernames_search
        ON users BEGIN
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
        reputation_rows = parse_reputation_folder(html_folder, user_id)
        return "success", (
            profile_to_row(user_id, profile), reputation_rows, profile
        )
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


def write_reputation_batch_to_db(cur, batch_rows: List[Tuple]):
    cur.executemany("""
        INSERT INTO reputation_history
        (reputation_id, user_id, giver_user_id, giver_username,
         giver_reputation, rating, rating_type, reason, updated_at,
         updated_sort, post_url, source_page)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(reputation_id) DO UPDATE SET
            user_id=excluded.user_id,
            giver_user_id=excluded.giver_user_id,
            giver_username=excluded.giver_username,
            giver_reputation=excluded.giver_reputation,
            rating=excluded.rating,
            rating_type=excluded.rating_type,
            reason=excluded.reason,
            updated_at=excluded.updated_at,
            updated_sort=excluded.updated_sort,
            post_url=excluded.post_url,
            source_page=excluded.source_page
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
        row, reputation_rows, profile = payload
        conn = init_users_db()
        cur = conn.cursor()
        write_user_batch_to_db(cur, [row])
        cur.execute("DELETE FROM reputation_history WHERE user_id = ?", (user_id,))
        if reputation_rows:
            write_reputation_batch_to_db(cur, reputation_rows)
        
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
            or (
                parts[2] not in USER_ARCHIVE_FILENAMES
                and not REPUTATION_FILENAME_RE.fullmatch(parts[2])
            )
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
    reputation_rows: List[Tuple] = []
    reputation_pages = []
    for name, info in entries.items():
        match = REPUTATION_FILENAME_RE.fullmatch(name)
        if match:
            reputation_pages.append((int(match.group(1)), info))
    for page_number, info in sorted(reputation_pages):
        reputation_rows.extend(
            parse_reputation_content(
                archive.read(info).decode("utf-8", errors="replace"),
                user_id,
                page_number,
            )
        )
    return profile_to_row(user_id, profile), reputation_rows, profile


def run_concurrent_migration(
    overwrite: bool = False,
    workers: int = DEFAULT_FOLDER_WORKERS,
    source: str = "auto",
    limit: Optional[int] = None,
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

    if limit is not None:
        user_ids.sort(
            key=lambda value: int(value) if value.isdigit() else sys.maxsize
        )
        user_ids = user_ids[:max(0, limit)]

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
    conn.execute("DROP INDEX IF EXISTS idx_reputation_user_updated")
    if overwrite:
        if limit is None:
            conn.execute("DELETE FROM reputation_history")
            conn.execute("DELETE FROM users")
        else:
            conn.executemany(
                "DELETE FROM reputation_history WHERE user_id = ?",
                ((user_id,) for user_id in users_to_process),
            )
            conn.executemany(
                "DELETE FROM users WHERE user_id = ?",
                ((user_id,) for user_id in users_to_process),
            )
    conn.commit()
    conn.close()

    chunk_size = 10000
    counts = {
        "done": 0,
        "skipped": skipped_count,
        "failed": 0,
        "reputation": 0,
    }

    # Open one writer and commit bounded batches.
    write_conn = sqlite3.connect(DB_PATH, timeout=300)
    write_conn.execute("PRAGMA journal_mode=OFF")
    write_conn.execute("PRAGMA synchronous=OFF")
    write_conn.execute("PRAGMA locking_mode=EXCLUSIVE")
    write_conn.execute("PRAGMA temp_store=MEMORY")
    write_conn.execute("PRAGMA cache_size=-262144")
    write_cur = write_conn.cursor()
    pending_rows = []
    pending_reputation_rows = []

    def flush_pending():
        if not pending_rows and not pending_reputation_rows:
            return
        if pending_rows:
            write_user_batch_to_db(write_cur, pending_rows)
        if pending_reputation_rows:
            write_reputation_batch_to_db(write_cur, pending_reputation_rows)
        write_conn.commit()
        pending_rows.clear()
        pending_reputation_rows.clear()

    def handle_result(status, res, pbar):
        if status == "success":
            pending_rows.append(res[0])
            pending_reputation_rows.extend(res[1])
            counts["done"] += 1
            counts["reputation"] += len(res[1])
            if len(pending_rows) >= 5000 or len(pending_reputation_rows) >= 50000:
                flush_pending()
        elif status == "missing":
            counts["skipped"] += 1
        else:
            counts["failed"] += 1
            print(f"\n{res}")
        pbar.update(1)

    with tqdm(
        total=total,
        desc="Profiles",
        unit="user",
        dynamic_ncols=True,
        mininterval=0.3,
        disable=not sys.stderr.isatty(),
    ) as pbar:
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
    print(f"⭐ Reputation records: {counts['reputation']:,}")
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
    print("  Indexing reputation history...")
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_reputation_user_updated
        ON reputation_history(user_id, updated_sort DESC, reputation_id DESC)
    """)
    conn.commit()
    print("FTS index rebuilt.")

    print("Running VACUUM (this may take a moment)...")
    conn.execute("VACUUM")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    print("VACUUM complete.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", type=str, default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--repair-alts",
        action="store_true",
        help="Only reparse alts.html and update the existing users.db rows",
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_FOLDER_WORKERS)
    parser.add_argument(
        "--db-path",
        default=DB_PATH,
        help="Output SQLite path (defaults to data/users.db)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Parse only the first N numeric user IDs (for benchmarking)",
    )
    parser.add_argument(
        "--source",
        choices=("auto", "zip", "folders"),
        default="auto",
        help="Bulk input source; auto prefers data/users.zip",
    )
    args = parser.parse_args()
    DB_PATH = os.path.abspath(args.db_path)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    if args.repair_alts:
        repair_alts_only(args.source)
    elif args.user:
        result = run_migration_on_user(args.user, args.overwrite)
        print(result)
    else:
        run_concurrent_migration(
            overwrite=args.overwrite,
            workers=args.workers,
            source=args.source,
            limit=args.limit,
        )
