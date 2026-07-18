# Usage:
#   python scripts/thread_parser.py                               # all threads
#   python scripts/thread_parser.py --overwrite                   # all threads, overwrite
#   python scripts/thread_parser.py --thread 1218438              # single thread
#   python scripts/thread_parser.py --thread 1218438 --overwrite  # single thread, overwrite

import re
import os
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from selectolax.parser import HTMLParser, Node
from bs4 import BeautifulSoup, NavigableString, Tag
from typing import TypedDict, List, Dict, Tuple, Optional, Any
from tqdm import tqdm
from datetime import datetime
import sqlite3

from v3rm_assets import (
    awards_to_html,
    normalize_image_path,
    render_stars_html,
    resolve_award_from_src,
    resolve_usergroup,
    split_glued_image_text,
    write_index_files,
)

# ── Repo-relative data paths ──────────────────────────────────────────────────
ROOT_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR    = os.path.join(ROOT_DIR, "data")
THREADS_DIR = os.path.join(DATA_DIR, "threads")
PARSED_DIR  = os.path.join(DATA_DIR, "parsed")
INDEX_DIR   = os.path.join(DATA_DIR, "index")
TITLES_JSON = os.path.join(INDEX_DIR, "titles.json")

# --- Regex Constraints ---
LIKE_BUTTONS_RE = re.compile(r'^like_buttons\d+$')
REPLIED_TO_RE = re.compile(r'\)\s*([^\n]+?)\s+Wrote:', re.IGNORECASE)
POST_DESCRIPTION_RE = re.compile(r'^\s+|\s+$')
REPLIED_TO_MESSAGE_DATE_RE = re.compile(r'\((.*?)\)')
PAGE_RE = re.compile(r'\d+')
FULL_WORDS_RE = re.compile(r"\S+")
URL_TOKEN_RE = re.compile(r"\[\[URL:([A-Za-z0-9_-]+)\]\]")
LAST_MODIFIED_RE = re.compile(r'\(This post was last modified:.*')
STATS_POSTS_RE   = re.compile(r'Posts:\s*([\d,]+)')
STATS_THREADS_RE = re.compile(r'Threads:\s*([\d,]+)')
STATS_JOINED_RE  = re.compile(r'Joined:\s*([^\d,][^R]+?)(?=Reputation|$)')

# --- Global Thread-Safe Variables for titles.json Indexing ---
titles_index = {}
index_lock = threading.Lock()

class Post(TypedDict):
    post_id: Optional[str]
    post_number: Optional[str]
    post_date: str
    unix_time: Optional[str]
    last_edit: Optional[str]
    likes: str
    dislikes: str
    author_username: Optional[str]
    author_id: Optional[str]
    user_title: Optional[str]
    user_rank: Optional[str]
    group_image: Optional[str]
    user_stars: Optional[int]
    user_group: Optional[Dict[str, Any]]
    awards: List[Dict[str, str]]
    reputation: Optional[str]
    post_count: Optional[str]
    thread_count: Optional[str]
    join_date: Optional[str]
    replied_to: Optional[str]
    replied_to_post_date: Optional[str]
    post_description: str
    post_description_start: Optional[str]
    replied_post_start: Optional[str]
    replies: List['Post']

class Thread(TypedDict):
    thread_path: str
    categories: List[str]
    title: Optional[str]
    thread_content: Optional[Post]
    replies_to_not_found_posts: List[Post]

def get_words_list(s: str) -> List[str]:
    return FULL_WORDS_RE.findall(s) if s else []

def get_text_start(text: str, p: float = 0.08) -> Optional[str]:
    if not text: return None
    return text[:round(len(text) * p + 1)]

def sortpages(v: str) -> int:
    match = PAGE_RE.search(v)
    return int(match.group()) if match else 0

def process_links_and_assets(soup: BeautifulSoup) -> BeautifulSoup:
    for img in soup.select('img[src]'):
        src = img.get('src', '').strip()
        alt = img.get('alt', '').strip()
        if src:
            local = normalize_image_path(src)
            label = alt or os.path.basename(local)
            img.replace_with(f" ![{label}]({local}) ")
    for a in soup.select('a[href]'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        image_path, remainder = split_glued_image_text(text)
        if image_path:
            replacement = f" ![{os.path.basename(image_path)}]({image_path}) "
            if remainder:
                replacement += remainder + " "  
            a.replace_with(replacement)
            continue
        if href:
            if text and text.lower() != href.lower() and '...' not in text:
                a.replace_with(f" [{text}]({href}) ")
            else:
                a.replace_with(f" {href} ")
    return soup


def parse_awards_from_stats(stats_node: Node) -> List[Dict[str, str]]:
    if not stats_node:
        return []
    soup = BeautifulSoup(stats_node.html, 'lxml')
    awards: List[Dict[str, str]] = []
    for link in soup.select('a[href*="awards.php"]'):
        img = link.find('img')
        if not img:
            continue
        src = img.get('src', '').strip()
        title = (img.get('alt') or link.get('title') or '').strip()
        view_match = re.search(r'view=(\d+)', link.get('href', ''))
        resolved = resolve_award_from_src(src, title)
        if view_match:
            resolved['view_id'] = view_match.group(1)
        awards.append(resolved)
    return awards


def parse_user_stars(author_info_node: Node) -> int:
    if not author_info_node:
        return 0
    soup = BeautifulSoup(author_info_node.html, 'lxml')
    return len(soup.select('img[src*="star"]'))

def bs4_get_text(node: Node) -> str:
    if not node: return ""
    soup = BeautifulSoup(node.html, 'lxml')
    soup = process_links_and_assets(soup)
    soup = extract_code_blocks(soup)
    return POST_DESCRIPTION_RE.sub('', soup.get_text(separator='\n'))

def extract_code_blocks(soup: BeautifulSoup):
    for block in soup.select("div.codeblock"):
        code_el = block.select_one("div.body code")
        if not code_el:
            block.decompose()
            continue
        code_text = code_el.get_text().replace("\r", "").strip()
        block.replace_with(f"\n[CODE_START]\n{code_text}\n[CODE_END]\n")
    return soup

def get_post_information(post: Node, all_like_buttons: List[Node], i: int, categories: List[str], debug: bool = False, page: str = None):
    if debug:
        print(f"Parsing {i+1} post in {page}")

    post_content_node = post.css_first('.post_content')
    post_body_node = post_content_node.css_first('.post_body.scaleimages') if post_content_node else None

    author_username = post_body_node.attributes.get('data-username') if post_body_node else None
    unix_time = post_body_node.attributes.get('data-dateline') if post_body_node else None

    author_id = None

    author_info_node = post.css_first('.author_information')
    info_node = post.css_first('.author_information .smalltext')

    if author_info_node:
        profile_link = author_info_node.css_first('a[href*="member.php?action=profile"]')

        if profile_link:
            href = profile_link.attributes.get('href', '')

            m = re.search(r'uid=(\d+)', href)
            if m:
                author_id = m.group(1)

    # post_id and post_number
    post_id = post.attributes.get('id', '').replace('post_', '')
    postlink = post.css_first('.postlink')
    post_number = None
    if postlink:
        m = re.match(r'#(\d+)', postlink.text(strip=True))
        post_number = m.group(1) if m else None

    # user_title, user_rank, reputation, post_count, thread_count, join_date, awards
    user_title = user_rank = reputation = post_count = thread_count = join_date = None
    group_image = None
    user_stars = 0
    awards: List[Dict[str, str]] = []

    if info_node:
        # preserve <br> line breaks
        raw_title = info_node.text(separator="\n", strip=True) or ""

        # first line is actual title
        user_title = raw_title.split("\n")[0].strip() or None

    userbar = post.css_first('.userbar-image')
    if userbar:
        user_rank = userbar.attributes.get('alt') or userbar.attributes.get('title') or None
        group_image = userbar.attributes.get('src') or None

    user_stars = parse_user_stars(author_info_node)

    rep_node = post.css_first('.reputation_positive, .reputation_negative')
    if rep_node:
        rep_text = rep_node.text(strip=True)
        reputation = rep_text if rep_text else None

    stats_node = post.css_first('.author_statistics')
    if stats_node:
        stats_text = stats_node.text(strip=True)
        m = STATS_POSTS_RE.search(stats_text)
        post_count = m.group(1).replace(',', '') if m else None
        m = STATS_THREADS_RE.search(stats_text)
        thread_count = m.group(1) if m else None
        m = STATS_JOINED_RE.search(stats_text)
        join_date = m.group(1).strip() if m else None
        awards = parse_awards_from_stats(stats_node)

    user_group = resolve_usergroup(user_rank=user_rank, user_title=user_title, group_image=group_image)

    post_date_node = post.css_first('.post_date')
    post_date = ""

    if post_date_node:
        post_date = post_date_node.text(separator=' ', strip=True)
        post_date = LAST_MODIFIED_RE.sub('', post_date).strip()

    if not post_date and unix_time:
        post_date = datetime.fromtimestamp(int(unix_time)).strftime("%m-%d-%Y, %I:%M %p")

    last_edit_node = post.css_first('.edited_post')
    last_edit = post_date

    if last_edit_node:
        raw = last_edit_node.text(separator=' ', strip=True)
        m = re.search(r'last modified:\s*(.*?)\s*by', raw, re.I)
        last_edit = m.group(1) if m else post_date

    like_buttons_node = all_like_buttons[i] if i < len(all_like_buttons) else None

    likes = dislikes = "0"
    if like_buttons_node:
        up = like_buttons_node.css_first('.fa.fa-thumbs-o-up')
        down = like_buttons_node.css_first('.fa.fa-thumbs-o-down')
        if up and up.parent and up.parent.parent:
            likes = up.parent.parent.text(strip=True)
        if down and down.parent and down.parent.parent:
            dislikes = down.parent.parent.text(strip=True)

    blockquote_element_text = None
    replied_to = replied_to_message_date = replied_post_start = None
    post_description = ""

    if post_body_node:
        soup = BeautifulSoup(post_body_node.html, 'lxml')
        soup = extract_code_blocks(soup)
        first_quote = True

        for quote in soup.find_all("blockquote"):
            cite = quote.find("cite")
            if not cite:
                quote.decompose()
                continue
            cite_text = cite.get_text(separator=' ', strip=True)
            if "quote:" in cite_text.lower():
                quote.decompose()
                continue
            if first_quote:
                m = REPLIED_TO_RE.search(cite_text)
                if m: replied_to = m.group(1).strip()
                dm = REPLIED_TO_MESSAGE_DATE_RE.search(cite_text)
                if dm: replied_to_message_date = dm.group(1)
                cite.decompose()
                blockquote_element_text = POST_DESCRIPTION_RE.sub('', quote.get_text(separator='\n'))
                replied_post_start = get_text_start(blockquote_element_text)
                first_quote = False
            quote.decompose()

        soup = process_links_and_assets(soup)
        post_description = POST_DESCRIPTION_RE.sub('', soup.get_text(separator='\n'))

    post_description_start = get_text_start(post_description)

    return (
        blockquote_element_text,
        {
            'post_id': post_id,
            'post_number': post_number,
            'post_date': post_date,
            'unix_time': unix_time,
            'last_edit': last_edit,
            'likes': likes,
            'dislikes': dislikes,
            'author_username': author_username,
            'author_id': author_id,
            'user_title': user_title,
            'user_rank': user_rank,
            'group_image': normalize_image_path(group_image) if group_image else None,
            'user_stars': user_stars,
            'user_group': user_group,
            'awards': awards,
            'reputation': reputation,
            'post_count': post_count,
            'thread_count': thread_count,
            'join_date': join_date,
            'replied_to': replied_to,
            'replied_to_post_date': replied_to_message_date,
            'post_description': post_description,
            'post_description_start': post_description_start,
            'replied_post_start': replied_post_start,
            'replies': []
        }
    )

def thread_parser(folder_path: str, debug: bool = False, save_after_parse: bool = False, path_to_save: str = '') -> Thread:
    if not os.path.exists(folder_path):
        raise FileNotFoundError(f"Target folder path context missing: {folder_path}")

    pages = sorted([f for f in os.listdir(folder_path) if f.endswith('.html')], key=sortpages)

    thread_tree: Thread = {
        'thread_path': folder_path,
        'categories': [],
        'title': None,
        'thread_content': None,
        'replies_to_not_found_posts': []
    }

    if pages:
        first_page_path = os.path.join(folder_path, pages[0])
        with open(first_page_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            target_indices = [311, 315]
            for idx in target_indices:
                if idx < len(lines):
                    match = re.search(r'>(.*?)</a>', lines[idx])
                    if match:
                        thread_tree['categories'].append(match.group(1).strip())

    posts_pos: Dict[Tuple[str, str], dict] = dict()
    got_thread_content_info = False

    for page in pages:
        file_path = os.path.join(folder_path, page)
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        tree = HTMLParser(content)
        posts = tree.css('.post')
        all_like_buttons = [el for el in tree.css('[id]') if LIKE_BUTTONS_RE.match(el.attributes.get('id', ''))]

        if not got_thread_content_info and len(posts) > 0:
            title_node = tree.css_first('head title')
            thread_tree['title'] = title_node.text().strip() if title_node else None

            _, thread_content_info = get_post_information(posts[0], all_like_buttons, 0, thread_tree['categories'], debug=debug, page=page)

            p_date, u_name, p_start = thread_content_info['post_date'], thread_content_info['author_username'], thread_content_info['post_description_start']
            posts_pos[(p_date, u_name)] = thread_content_info
            posts_pos[(p_date, p_start)] = thread_content_info
            thread_tree['thread_content'] = thread_content_info

        start_idx = 1 if not got_thread_content_info else 0

        for i in range(start_idx, len(posts)):
            blockquote_element_text, post_info = get_post_information(posts[i], all_like_buttons, i, thread_tree['categories'], debug=debug, page=page)

            p_date, u_name, p_start = post_info['post_date'], post_info['author_username'], post_info['post_description_start']
            posts_pos[(p_date, u_name)] = post_info
            posts_pos[(p_date, p_start)] = post_info

            r_date, r_to, r_start = post_info["replied_to_post_date"], post_info['replied_to'], post_info['replied_post_start']

            if r_date and r_to:
                replied_post = posts_pos.get((r_date, r_to)) or posts_pos.get((r_date, r_start))
                if replied_post:
                    replied_post['replies'].append(post_info)
                else:
                    thread_tree['replies_to_not_found_posts'].append(post_info)
                    post_info['quote'] = blockquote_element_text
            else:
                if thread_tree['thread_content']:
                    thread_tree['thread_content']['replies'].append(post_info)

        got_thread_content_info = True

    if save_after_parse and thread_tree['thread_content']:
        if not path_to_save:
            _, folder_name = os.path.split(folder_path.rstrip('/\\'))
            path_to_save = os.path.join(PARSED_DIR, f'parsed_thread_{folder_name}.json')
        os.makedirs(os.path.dirname(path_to_save), exist_ok=True)
        with open(path_to_save, 'w', encoding='utf-8') as f:
            json.dump(thread_tree, f, ensure_ascii=False, indent=2)

    return thread_tree


def load_thread_json_meta(html_folder: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    try:
        with open(os.path.join(html_folder, "thread.json"), "r", encoding="utf-8") as f:
            meta = json.load(f)
        author_info = meta.get("author") or {}
        return (
            (meta.get("title") or "").strip() or None,
            author_info.get("username") or None,
            author_info.get("id") or None,
        )
    except Exception:
        return None, None, None


def run_migration_on_file(file_name: str, target_dir: str = PARSED_DIR, overwrite: bool = False):
    file_path = os.path.join(target_dir, file_name)

    try:
        match = re.search(r"parsed_thread_(\d+)\.json", file_name)
        thread_id = match.group(1) if match else None
        if not thread_id:
            return f"Skipping {file_name}"

        html_folder = os.path.join(THREADS_DIR, thread_id)

        # parse html into structured dict
        data = thread_parser(html_folder, save_after_parse=False)

        # store into SQLite DB
        db_path = os.path.join(DATA_DIR, "threads.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        cur = conn.cursor()

        # initialize tables
        cur.execute('''
        CREATE TABLE IF NOT EXISTS threads (
            tid TEXT PRIMARY KEY,
            title TEXT,
            author_username TEXT,
            author_id TEXT,
            categories TEXT,
            date TEXT,
            reply_count INTEGER
        )
        ''')

        cur.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            post_id TEXT PRIMARY KEY,
            tid TEXT,
            post_number TEXT,
            unix_time TEXT,
            post_date TEXT,
            last_edit TEXT,
            likes TEXT,
            dislikes TEXT,
            author_username TEXT,
            author_id TEXT,
            user_title TEXT,
            user_rank TEXT,
            user_group TEXT,
            user_stars INTEGER,
            reputation TEXT,
            post_count TEXT,
            thread_count TEXT,
            join_date TEXT,
            replied_to TEXT,
            replied_to_post_date TEXT,
            post_description TEXT,
            is_op INTEGER,
            awards TEXT
        )
        ''')

        cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_tid ON posts(tid)")

        # FTS5 virtual table for searching posts
        try:
            cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts_posts USING fts5(post_description, author_username, author_id, tid, pid)")
        except sqlite3.OperationalError:
            # FTS5 might not be available on all builds; ignore if it fails
            pass

        categories = data.get("categories", []) or ["Uncategorized"]

        meta_title, meta_username, meta_author_id = load_thread_json_meta(html_folder)

        clean_title = (meta_title or (data.get("title") or "Untitled Thread")).strip()
        author = (meta_username or (data.get("thread_content") or {}).get("author_username") or "Unknown User")
        author_id = meta_author_id

        with index_lock:
            titles_index[thread_id] = {
                "title": clean_title,
                "author": author,
                "author_id": author_id,
                "categories": categories
            }

        # upsert thread row
        thread_date = None
        thread_content = data.get("thread_content") or {}
        if thread_content:
            thread_date = thread_content.get("post_date")

        reply_count = len(thread_content.get("replies", [])) if thread_content else 0

        cur.execute("INSERT OR REPLACE INTO threads(tid, title, author_username, author_id, categories, date, reply_count) VALUES (?,?,?,?,?,?,?)",
                    (thread_id, clean_title, author, author_id, json.dumps(categories, ensure_ascii=False), thread_date, reply_count))

        # collect posts (flatten replies)
        def collect_posts(pinfo, is_op=False):
            out = []
            out.append((pinfo, is_op))
            for r in pinfo.get("replies", []):
                out.extend(collect_posts(r, is_op=False))
            return out

        posts_to_insert = []
        if thread_content:
            posts_to_insert = collect_posts(thread_content, is_op=True)

        for post_info, is_op in posts_to_insert:
            # generate stable pid
            post_id = post_info.get("post_id") or f"{thread_id}_{post_info.get('post_number') or ''}".strip("_")

            # strip user_group image keys if present
            ug = post_info.get("user_group")
            if isinstance(ug, dict):
                ug = {k: v for k, v in ug.items() if k != 'image'}
            else:
                ug = None

            # strip awards to name+title only
            raw_awards = post_info.get("awards") or []
            awards_simple = []
            for a in raw_awards:
                name = a.get('name') or a.get('title') or ''
                title = a.get('title') or a.get('name') or ''
                awards_simple.append({"name": name, "title": title})

            cur.execute("INSERT OR REPLACE INTO posts(post_id, tid, post_number, unix_time, post_date, last_edit, likes, dislikes, author_username, author_id, user_title, user_rank, user_group, user_stars, reputation, post_count, thread_count, join_date, replied_to, replied_to_post_date, post_description, is_op, awards) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            post_id,
                            thread_id,
                            post_info.get('post_number'),
                            post_info.get('unix_time'),
                            post_info.get('post_date'),
                            post_info.get('last_edit'),
                            post_info.get('likes'),
                            post_info.get('dislikes'),
                            post_info.get('author_username'),
                            post_info.get('author_id'),
                            post_info.get('user_title'),
                            post_info.get('user_rank'),
                            json.dumps(ug, ensure_ascii=False) if ug is not None else None,
                            post_info.get('user_stars'),
                            post_info.get('reputation'),
                            post_info.get('post_count'),
                            post_info.get('thread_count'),
                            post_info.get('join_date'),
                            post_info.get('replied_to'),
                            post_info.get('replied_to_post_date'),
                            post_info.get('post_description'),
                            1 if is_op else 0,
                            json.dumps(awards_simple, ensure_ascii=False)
                        ))

            # insert into FTS table if available
            try:
                cur.execute("INSERT INTO fts_posts(rowid, post_description, author_username, author_id, tid, pid) VALUES (last_insert_rowid(),?,?,?,?,?)",
                            (post_info.get('post_description') or '', post_info.get('author_username'), post_info.get('author_id'), thread_id, post_id))
            except sqlite3.OperationalError:
                # FTS not available or schema different; ignore
                pass

        conn.commit()
        conn.close()

        return True

    except Exception as e:
        return f"Error {file_name}: {e}"

def run_concurrent_migration(target_dir: str = PARSED_DIR, threads: int = 12, overwrite: bool = False):
    os.makedirs(target_dir, exist_ok=True)

    all_thread_ids = [e.name for e in os.scandir(THREADS_DIR) if e.is_dir()]
    tasks = [f"parsed_thread_{tid}.json" for tid in all_thread_ids]

    total_files = len(tasks)
    if total_files == 0:
        print(f"No threads found in '{THREADS_DIR}'.")
        return

    print(f"🚀 Starting work on {total_files} threads (using {threads} workers)...")

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(run_migration_on_file, fname, target_dir, overwrite): fname for fname in tasks}

        counts = {"done": 0, "failed": 0, "skipped": 0}
        counts_lock = threading.Lock()

        with tqdm(total=total_files, desc="Processing", unit="file", dynamic_ncols=True, leave=True) as pbar:
            for future in as_completed(futures):
                result = future.result()
                with counts_lock:
                    if result is True:
                        counts["done"] += 1
                    elif isinstance(result, str) and result.startswith("Skipped"):
                        counts["skipped"] += 1
                    else:
                        counts["failed"] += 1
                pbar.update(1)

    print("\n" + "="*50)
    print(f"✅ Finished: {counts['done']:,}")
    print(f"⏭ Skipped:  {counts['skipped']:,}")
    print(f"❌ Failed:   {counts['failed']:,}")
    print("="*50)

    os.makedirs(INDEX_DIR, exist_ok=True)
    write_index_files()
    print("\n💾 Saving updated, structured titles.json...")
    with open(TITLES_JSON, "w", encoding="utf-8") as f:
        json.dump(titles_index, f, ensure_ascii=False, indent=2)

    print(f"🎉 Complete! Generated {len(titles_index):,} indexed entries inside {TITLES_JSON}.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--thread", type=str, default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.thread:
        os.makedirs(PARSED_DIR, exist_ok=True)
        result = run_migration_on_file(f"parsed_thread_{args.thread}.json", PARSED_DIR, overwrite=args.overwrite)
        print(f"Result: {result}")
    else:
        run_concurrent_migration(overwrite=args.overwrite)