# Usage:
#   python scripts/profile_parser.py                     # all users (resume mode)
#   python scripts/profile_parser.py --overwrite         # force reparse all
#   python scripts/profile_parser.py --user 123456       # single user
#   python scripts/profile_parser.py --user 123456 --overwrite

import json
import os
import re
import sys
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

# Better output for tqdm
sys.stdout.reconfigure(line_buffering=True)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
USERS_DIR = os.path.join(DATA_DIR, "users")
INDEX_DIR = os.path.join(DATA_DIR, "index")
DB_PATH = os.path.join(DATA_DIR, "users.db")


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


def parse_alts_html(alts_path: str) -> List[Dict[str, str]]:
    alts: List[Dict[str, str]] = []
    if not os.path.exists(alts_path):
        return alts

    with open(alts_path, "r", encoding="utf-8") as f:
        tree = HTMLParser(f.read())

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


def parse_past_usernames_html(path: str) -> List[Dict[str, str]]:
    past: List[Dict[str, str]] = []
    if not os.path.exists(path):
        return past

    with open(path, "r", encoding="utf-8") as f:
        tree = HTMLParser(f.read())

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


def parse_profile_html(html_path: str, user_id: str) -> Dict[str, Any]:
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

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

    alts_path = os.path.join(os.path.dirname(html_path), "alts.html")
    alts = parse_alts_html(alts_path)

    past_usernames_path = os.path.join(os.path.dirname(html_path), "past_usernames.html")
    past_usernames = parse_past_usernames_html(past_usernames_path)

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


def init_users_db():
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
            past_usernames TEXT
        );
    ''')

    try:
        conn.execute("ALTER TABLE users ADD COLUMN past_usernames TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts_users USING fts5(user_id, username, user_title, user_rank, past_usernames)")
    except sqlite3.OperationalError:
        pass
    return conn


# ── STEP A: Pure parallel worker (NO SQLITE WRITING) ───────────
def parse_user_worker(user_id: str) -> Tuple[str, Any]:
    """Worker Job: Only parses text files via selectolax into raw structured tuples."""
    html_folder = os.path.join(USERS_DIR, user_id)
    profile_html = os.path.join(html_folder, "profile.html")

    if not os.path.exists(profile_html):
        return "missing", f"Skipping {user_id}: profile.html missing"

    try:
        profile = parse_profile_html(profile_html, user_id)

        ug = profile.get('user_group')
        if isinstance(ug, dict):
            ug = {k: v for k, v in ug.items() if k != 'image'}

        row = (
            user_id, profile.get('username'), profile.get('user_title'), profile.get('user_rank'),
            json.dumps(ug) if ug is not None else None, profile.get('user_stars'), profile.get('reputation'),
            profile.get('post_count'), profile.get('thread_count'), profile.get('joined'),
            profile.get('last_visit'), profile.get('time_online'),
            json.dumps(profile.get('awards')) if profile.get('awards') is not None else None,
            json.dumps(profile.get('alts')) if profile.get('alts') is not None else None,
            json.dumps(profile.get('past_usernames')) if profile.get('past_usernames') is not None else None,
        )
        return "success", (row, profile)
    except Exception as exc:
        return "error", f"Error parsing {user_id}: {exc}"


# ── STEP B: Direct linear single-threaded DB flush ──────────────
def write_user_batch_to_db(cur, batch_rows: List[Tuple]):
    cur.executemany("""
        INSERT OR REPLACE INTO users 
        (user_id, username, user_title, user_rank, user_group, user_stars, reputation, post_count, thread_count, 
         joined, last_visit, time_online, awards, alts, past_usernames)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
        if fts_exists:
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


def run_concurrent_migration(overwrite: bool = False, workers: int = 12):
    if not os.path.isdir(USERS_DIR):
        print(f"No users directory at {USERS_DIR}")
        return

    user_ids = [entry.name for entry in os.scandir(USERS_DIR) if entry.is_dir()]
    total = len(user_ids)

    if total == 0:
        print("No user folders found.")
        return

    print(f"Found {total:,} user profiles. Starting migration with {workers} workers...")

    conn = init_users_db()
    if not overwrite:
        print("Scoping DB to skip processed users...")
        existing_ids = {r[0] for r in conn.execute("SELECT user_id FROM users").fetchall()}
        users_to_process = [uid for uid in user_ids if uid not in existing_ids]
        skipped_count = len(user_ids) - len(users_to_process)
        print(f"Skipping {skipped_count:,} already matched profiles.")
    else:
        users_to_process = user_ids
        skipped_count = 0
    conn.close()

    chunk_size = 10000
    counts = {"done": 0, "skipped": skipped_count, "failed": 0}

    # Open single synchronous pipeline for sequential insertion
    write_conn = sqlite3.connect(DB_PATH, timeout=300)
    write_conn.execute("PRAGMA journal_mode=WAL")
    write_conn.execute("PRAGMA synchronous=OFF")
    write_cur = write_conn.cursor()

    with tqdm(total=total, desc="Profiles", unit="user", dynamic_ncols=True, mininterval=0.3) as pbar:
        if skipped_count > 0:
            pbar.update(skipped_count)

        for i in range(0, len(users_to_process), chunk_size):
            chunk = users_to_process[i:i + chunk_size]
            db_rows = []

            with ProcessPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(parse_user_worker, uid): uid for uid in chunk}

                for future in as_completed(futures):
                    status, res = future.result()
                    if status == "success":
                        db_rows.append(res[0])
                        counts["done"] += 1
                    elif status == "missing":
                        counts["skipped"] += 1
                    else:
                        counts["failed"] += 1
                        print(f"\n{res}")
                    pbar.update(1)

            if db_rows:
                write_user_batch_to_db(write_cur, db_rows)
                write_conn.commit()

    write_conn.close()

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
    
    conn.execute("DROP TABLE IF EXISTS fts_users")
    conn.execute("""CREATE VIRTUAL TABLE fts_users
                    USING fts5(user_id, username, user_title, user_rank, past_usernames)""")
    print("  Populating fts_users...")
    conn.execute("""INSERT INTO fts_users(user_id, username, user_title, user_rank, past_usernames)
                    SELECT user_id, username, user_title, user_rank,
                    (SELECT GROUP_CONCAT(json_extract(value, '$.username'), ' ')
                     FROM json_each(COALESCE(past_usernames, '[]')))
                    FROM users""")
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
    args = parser.parse_args()

    if args.user:
        result = run_migration_on_user(args.user, args.overwrite)
        print(result)
    else:
        run_concurrent_migration(overwrite=args.overwrite, workers=12)