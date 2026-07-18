# Usage:
#   python scripts/profile_parser.py                     # all users
#   python scripts/profile_parser.py --overwrite         # all users, overwrite
#   python scripts/profile_parser.py --user 123456      # single user
#   python scripts/profile_parser.py --user 123456 --overwrite

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from tqdm import tqdm
import sqlite3

from v3rm_assets import (
    normalize_image_path,
    resolve_award_from_src,
    resolve_usergroup,
    write_index_files,
)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
USERS_DIR = os.path.join(DATA_DIR, "users")
PARSED_DIR = os.path.join(DATA_DIR, "parsed")
INDEX_DIR = os.path.join(DATA_DIR, "index")
USERS_JSON = os.path.join(INDEX_DIR, "users.json")

users_index: Dict[str, Dict[str, Any]] = {}
index_lock = threading.Lock()


def parse_awards_from_profile(soup: BeautifulSoup) -> List[Dict[str, str]]:
    awards: List[Dict[str, str]] = []
    for table in soup.select("table.tborder"):
        header = table.select_one(".thead")
        if not header or "award" not in header.get_text(strip=True).lower():
            continue
        for link in table.select('a[href*="awards.php"]'):
            img = link.find("img")
            if not img:
                continue
            src = img.get("src", "").strip()
            title = (img.get("alt") or link.get("title") or "").strip()
            awards.append(resolve_award_from_src(src, title))
    for link in soup.select('.author_statistics a[href*="awards.php"], .profile_header a[href*="awards.php"]'):
        img = link.find("img")
        if not img:
            continue
        src = img.get("src", "").strip()
        title = (img.get("alt") or link.get("title") or "").strip()
        resolved = resolve_award_from_src(src, title)
        if resolved not in awards:
            awards.append(resolved)
    return awards


def extract_table_value(soup: BeautifulSoup, label: str) -> Optional[str]:
    for row in soup.select("tr"):
        strong = row.select_one("td strong")
        if not strong:
            continue
        if strong.get_text(strip=True).rstrip(":").lower() == label.lower():
            cells = row.select("td")
            if len(cells) >= 2:
                return cells[1].get_text(" ", strip=True)
    return None


def parse_alts_html(alts_path: str) -> List[Dict[str, str]]:
    """Parse alts.html and return a list of alternate accounts."""
    alts: List[Dict[str, str]] = []

    if not os.path.exists(alts_path):
        return alts

    with open(alts_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "lxml")

    # Find the Alternate Accounts table
    for table in soup.select("table.tborder"):
        header = table.select_one(".thead")
        if not header or "alternate" not in header.get_text(strip=True).lower():
            continue

        # Each alt is a <tr> with 3 <td>s (User, Certainty, Last matched)
        for row in table.select("tr"):
            cells = row.select("td")
            if len(cells) != 3:
                continue

            # Skip header rows
            if cells[0].select_one(".smalltext"):
                continue

            user_cell = cells[0]
            certainty_cell = cells[1]
            last_matched_cell = cells[2]

            # Extract UID from the href
            link = user_cell.select_one("a[href*='uid=']")
            if not link:
                continue

            href = link.get("href", "")
            uid_match = re.search(r"uid=(\d+)", href)
            if not uid_match:
                continue
            alt_uid = uid_match.group(1)

            # Extract username — could be in a <strong> or just be "Deleted user"
            strong = user_cell.select_one("strong")
            if strong:
                alt_username = strong.get_text(strip=True)
            else:
                alt_username = link.get_text(strip=True) or "Deleted user"

            # Certainty: text inside .box span
            certainty_span = certainty_cell.select_one(".box")
            certainty = certainty_span.get_text(strip=True) if certainty_span else None

            # Last matched: raw text in the 3rd cell
            last_matched = last_matched_cell.get_text(strip=True) or None

            alts.append({
                "uid": alt_uid,
                "username": alt_username,
                "certainty": certainty,
                "last_matched": last_matched,
            })

    return alts


def parse_profile_html(html_path: str, uid: str) -> Dict[str, Any]:
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

    soup = BeautifulSoup(content, "lxml")

    title_match = re.search(
        r"Profile of (.+?)(?:\s*-|\s*$)",
        soup.title.string if soup.title else ""
    )
    username = title_match.group(1).strip() if title_match else None

    header = soup.select_one("fieldset.profile_header")

    usertitle = None
    group_image = None
    user_stars = 0

    if header:
        name_el = header.select_one(".largetext strong")

        if name_el:
            inner = name_el.select_one("[style]")

            if inner:
                username = inner.get_text(strip=True) or username
            else:
                username = name_el.get_text(strip=True) or username

        smalltext = header.select_one(".smalltext")

        if smalltext:
            for node in smalltext.contents:
                if getattr(node, "name", None) is None:
                    text = str(node).strip()

                    if text.startswith("(") and text.endswith(")"):
                        usertitle = text.strip("()")
                        break

            user_stars = len(
                smalltext.select('img[src*="star"]')
            )

            userbar = smalltext.select_one(
                ".userbar-image, img[src*='UserBars']"
            )

            if userbar:
                group_image = userbar.get("src")
            elif smalltext.select("img[src*='UserBars']"):
                group_image = smalltext.select(
                    "img[src*='UserBars']"
                )[0].get("src")

    if not username:
        username = f"User {uid}"

    joined = (
        extract_table_value(soup, "Joined")
        or extract_table_value(soup, "Joined:")
    )

    last_visit = extract_table_value(soup, "Last Visit")
    posts_raw = extract_table_value(soup, "Total Posts")
    threads_raw = extract_table_value(soup, "Total Threads")
    time_online = extract_table_value(soup, "Time Spent Online")

    reputation = None
    rep_el = soup.select_one(
        ".reputation_positive, .reputation_negative, .reputation_neutral"
    )
    if rep_el:
        reputation = rep_el.get_text(strip=True)

    awards = parse_awards_from_profile(soup)

    user_rank = None
    userbar_el = soup.select_one(".userbar-image")
    if userbar_el:
        user_rank = userbar_el.get("alt") or userbar_el.get("title")
        group_image = group_image or userbar_el.get("src")

    user_group = resolve_usergroup(
        user_rank=user_rank,
        user_title=usertitle,
        group_image=group_image,
    )

    # Parse alts from sibling alts.html file
    alts_path = os.path.join(os.path.dirname(html_path), "alts.html")
    alts = parse_alts_html(alts_path)

    profile = {
        "uid": uid,
        "username": username,
        "usertitle": usertitle,
        "user_rank": user_rank,
        "group_image": normalize_image_path(group_image) if group_image else None,
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
        "profile_path": html_path,
    }

    return profile


def run_migration_on_user(uid: str, overwrite: bool = False) -> Any:
    html_folder = os.path.join(USERS_DIR, uid)
    profile_html = os.path.join(html_folder, "profile.html")

    if not os.path.exists(profile_html):
        return f"Skipping {uid}: profile.html missing"

    try:
        profile = parse_profile_html(profile_html, uid)

        # persist into SQLite
        db_path = os.path.join(DATA_DIR, "users.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        cur = conn.cursor()

        cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            uid TEXT PRIMARY KEY,
            username TEXT,
            usertitle TEXT,
            user_rank TEXT,
            reputation TEXT,
            post_count TEXT,
            thread_count TEXT,
            user_stars INTEGER,
            user_group TEXT,
            joined TEXT,
            last_visit TEXT,
            time_online TEXT,
            awards TEXT,
            alts TEXT
        )
        ''')

        # simplify user_group by removing image key
        ug = profile.get('user_group')
        if isinstance(ug, dict):
            ug = {k: v for k, v in ug.items() if k != 'image'}

        # simplify awards to name+title
        raw_awards = profile.get('awards') or []
        awards_simple = []
        for a in raw_awards:
            name = a.get('name') or a.get('title') or ''
            title = a.get('title') or a.get('name') or ''
            awards_simple.append({"name": name, "title": title})

        cur.execute("INSERT OR REPLACE INTO users(uid, username, usertitle, user_rank, reputation, post_count, thread_count, user_stars, user_group, joined, last_visit, time_online, awards, alts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uid,
                        profile.get('username'),
                        profile.get('usertitle'),
                        profile.get('user_rank'),
                        profile.get('reputation'),
                        profile.get('post_count'),
                        profile.get('thread_count'),
                        profile.get('user_stars'),
                        json.dumps(ug, ensure_ascii=False) if ug is not None else None,
                        profile.get('joined'),
                        profile.get('last_visit'),
                        profile.get('time_online'),
                        json.dumps(awards_simple, ensure_ascii=False),
                        json.dumps(profile.get('alts') or [], ensure_ascii=False)
                    ))

        conn.commit()
        conn.close()

        with index_lock:
            users_index[uid] = {
                "username": profile["username"],
                "usertitle": profile.get("usertitle"),
                "user_rank": profile.get("user_rank"),
                "reputation": profile.get("reputation"),
                "post_count": profile.get("post_count"),
                "thread_count": profile.get("thread_count"),
                "awards_count": len(awards_simple),
                "alts_count": len(profile.get("alts") or []),
            }

        return True

    except Exception as exc:
        return f"Error {uid}: {exc}"


def run_concurrent_migration(threads: int = 12, overwrite: bool = False):
    if not os.path.isdir(USERS_DIR):
        print(f"No users directory at {USERS_DIR}")
        return

    user_ids = [entry.name for entry in os.scandir(USERS_DIR) if entry.is_dir()]
    total = len(user_ids)

    if total == 0:
        print("No user folders found.")
        return

    print(f"Parsing {total:,} user profiles ({threads} workers)...")

    counts = {"done": 0, "failed": 0, "skipped": 0}
    counts_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = {
            executor.submit(run_migration_on_user, uid, overwrite): uid
            for uid in user_ids
        }

        with tqdm(total=total, desc="Profiles", unit="user", dynamic_ncols=True) as pbar:
            for future in as_completed(futures):
                result = future.result()

                with counts_lock:
                    if result is True:
                        counts["done"] += 1
                    elif isinstance(result, str) and result.startswith("Skipping"):
                        counts["skipped"] += 1
                    else:
                        counts["failed"] += 1

                pbar.update(1)

    save_users_index()

    print(f"Done: {counts['done']:,} | Skipped: {counts['skipped']:,} | Failed: {counts['failed']:,}")
    print(f"Wrote {USERS_JSON} ({len(users_index):,} users)")


def save_users_index():
    os.makedirs(INDEX_DIR, exist_ok=True)
    write_index_files()

    with open(USERS_JSON, "w", encoding="utf-8") as f:
        json.dump(users_index, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--user", type=str, default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.user:
        save_users_index()

        print(f"💾 Index updated ({len(users_index):,} users)")
    else:
        run_concurrent_migration()