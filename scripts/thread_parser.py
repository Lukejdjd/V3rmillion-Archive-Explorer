# Usage:
#   python scripts/thread_parser.py                               # all threads (skips already in DB)
#   python scripts/thread_parser.py --overwrite                   # force reprocess all
#   python scripts/thread_parser.py --thread 1218438              # single thread
#   python scripts/thread_parser.py --thread 1218438 --overwrite  # single thread, force

import re
import os
import json
import threading
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from selectolax.parser import HTMLParser, Node
from bs4 import BeautifulSoup
from typing import TypedDict, List, Dict, Tuple, Optional, Any
from tqdm import tqdm
from datetime import datetime
import sqlite3

from v3rm_assets import (
    normalize_image_path,
    resolve_usergroup,
    split_glued_image_text,
)

sys.stdout.reconfigure(line_buffering=True)

# ── Paths ──────────────────────────────────────────────────
ROOT_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR    = os.path.join(ROOT_DIR, "data")
THREADS_DIR = os.path.join(DATA_DIR, "threads")
INDEX_DIR   = os.path.join(DATA_DIR, "index")
DB_PATH     = os.path.join(DATA_DIR, "threads.db")

# --- Regex ---
LIKE_BUTTONS_RE = re.compile(r'^like_buttons\d+$')
REPLIED_TO_RE = re.compile(r'\)\s*([^\n]+?)\s+Wrote:', re.IGNORECASE)
POST_DESCRIPTION_RE = re.compile(r'^\s+|\s+$')
REPLIED_TO_MESSAGE_DATE_RE = re.compile(r'\((.*?)\)')
PAGE_RE = re.compile(r'\d+')
LAST_MODIFIED_RE = re.compile(r'\(This post was last modified:.*')

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
    user_group: Optional[Dict[str, Any]]
    user_stars: Optional[int]
    reputation: Optional[str]
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


def get_text_start(text: str, p: float = 0.08) -> Optional[str]:
    if not text:
        return None
    return text[:round(len(text) * p + 1)]


def parse_user_stars(author_info_node) -> int:
    if not author_info_node:
        return 0
    soup = BeautifulSoup(author_info_node.html, 'lxml')
    return len(soup.select('img[src*="star"]'))



    match = PAGE_RE.search(v)
    return int(match.group()) if match else 0


def transform_spoilers(soup: BeautifulSoup) -> BeautifulSoup:
    # PATCH: single-pass bottom-up instead of O(n²) while-loop rescan
    for wrap in soup.find_all("div", class_="spoiler_wrap"):
        body = wrap.find("div", class_="spoiler_body")
        new_tag = soup.new_tag("div", attrs={"class": "v3rm-spoiler"})

        if body:
            new_tag.extend(list(body.children))

        wrap.replace_with(new_tag)

    return soup


def transform_quotes(soup: BeautifulSoup) -> BeautifulSoup:
    # PATCH: single-pass bottom-up instead of O(n²) while-loop rescan.
    # find_all returns nodes in document order; reversing processes
    # innermost blockquotes first so parents see already-transformed children.
    for quote in reversed(soup.find_all("blockquote")):
        cite = quote.find("cite", recursive=False)
        author_text = ""

        if cite:
            author_text = cite.get_text(separator=" ", strip=True)
            cite.decompose()

        new_quote = soup.new_tag("blockquote")
        new_quote["class"] = "v3rm-quote"

        if author_text:
            author = soup.new_tag("cite")
            author["class"] = "v3rm-quote-author"
            author.string = author_text
            new_quote.append(author)

        new_quote.extend(list(quote.children))
        quote.replace_with(new_quote)

    return soup


# ── Asset / link processing ─────────────────────────────────

def process_links_and_assets(soup: BeautifulSoup) -> BeautifulSoup:
    for img in soup.select('img[src]'):
        src = img.get('src', '').strip()

        if src:
            local = normalize_image_path(src)
            img.replace_with(f" ![]({local}) ")

    for a in soup.select('a[href]'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)

        image_path, remainder = split_glued_image_text(text)

        if image_path:
            replacement = (
                f" ![{os.path.basename(image_path)}]({image_path}) "
            )

            if remainder:
                replacement += remainder + " "

            a.replace_with(replacement)
            continue

        if href:
            if (
                text
                and text.lower() != href.lower()
                and '.' not in text
            ):
                a.replace_with(f" [{text}]({href}) ")
            else:
                a.replace_with(f" {href} ")

    return soup


def extract_code_blocks(soup: BeautifulSoup) -> BeautifulSoup:
    for block in soup.select("div.codeblock"):
        code_el = block.select_one("div.body code")

        if not code_el:
            block.decompose()
            continue

        code_text = code_el.get_text().replace("\r", "").strip()

        block.replace_with(
            f"\n[CODE_START]\n{code_text}\n[CODE_END]\n"
        )

    return soup


def bs4_get_text(node: Node) -> str:
    if not node:
        return ""

    soup = BeautifulSoup(node.html, 'lxml')
    soup = process_links_and_assets(soup)
    soup = extract_code_blocks(soup)

    return POST_DESCRIPTION_RE.sub(
        '',
        soup.get_text(separator='\n')
    )


def process_post_body(soup: BeautifulSoup) -> BeautifulSoup:
    soup = extract_code_blocks(soup)
    soup = transform_quotes(soup)
    soup = transform_spoilers(soup)
    soup = process_links_and_assets(soup)

    return soup


def get_post_information(
    post: Node,
    all_like_buttons: List[Node],
    i: int,
    categories: List[str],
    debug: bool = False,
    page: str = None
):
    post_content_node = post.css_first('.post_content')

    post_body_node = (
        post_content_node.css_first('.post_body.scaleimages')
        if post_content_node else None
    )

    author_username = (
        post_body_node.attributes.get('data-username')
        if post_body_node else None
    )

    unix_time = (
        post_body_node.attributes.get('data-dateline')
        if post_body_node else None
    )

    author_id = None
    author_info_node = post.css_first('.author_information')

    if author_info_node:
        profile_link = author_info_node.css_first(
            'a[href*="member.php?action=profile"]'
        )

        if profile_link:
            m = re.search(
                r'uid=(\d+)',
                profile_link.attributes.get('href', '')
            )

            if m:
                author_id = m.group(1)

    post_id = post.attributes.get('id', '').replace('post_', '')

    # ── User rank/title/group/stars at post time ────────────────────────
    user_title = user_rank = None
    user_stars = 0
    group_image = None

    info_node = post.css_first('.author_information .smalltext')
    if info_node:
        raw_title = info_node.text(separator='\n', strip=True) or ''
        user_title = raw_title.split('\n')[0].strip() or None

    userbar = post.css_first('.userbar-image')
    if userbar:
        user_rank = userbar.attributes.get('alt') or userbar.attributes.get('title') or None
        group_image = userbar.attributes.get('src') or None

    user_group = resolve_usergroup(group_image, user_rank)
    user_stars = parse_user_stars(post.css_first('.author_information'))

    reputation = None
    rep_node = post.css_first('.reputation_positive, .reputation_negative, .reputation_neutral')
    if rep_node:
        rep_text = rep_node.text(strip=True)
        reputation = rep_text if rep_text else None
    # ────────────────────────────────────────────────────────────────────

    postlink = post.css_first('.postlink')
    post_number = None

    if postlink:
        m = re.match(r'#(\d+)', postlink.text(strip=True))
        post_number = m.group(1) if m else None

    post_date_node = post.css_first('.post_date')
    post_date = ""

    if post_date_node:
        post_date = post_date_node.text(
            separator=' ',
            strip=True
        )

        post_date = LAST_MODIFIED_RE.sub(
            '',
            post_date
        ).strip()

    if not post_date and unix_time:
        post_date = datetime.fromtimestamp(
            int(unix_time)
        ).strftime("%m-%d-%Y, %I:%M %p")

    last_edit_node = post.css_first('.edited_post')
    last_edit = post_date

    if last_edit_node:
        raw = last_edit_node.text(separator=' ', strip=True)

        m = re.search(
            r'last modified:\s*(.*?)\s*by',
            raw,
            re.I
        )

        last_edit = m.group(1) if m else post_date

    like_buttons_node = (
        all_like_buttons[i]
        if i < len(all_like_buttons)
        else None
    )

    likes = dislikes = "0"

    if like_buttons_node:
        up = like_buttons_node.css_first('.fa.fa-thumbs-o-up')
        down = like_buttons_node.css_first('.fa.fa-thumbs-o-down')

        if up and up.parent and up.parent.parent:
            likes = up.parent.parent.text(strip=True)

        if down and down.parent and down.parent.parent:
            dislikes = down.parent.parent.text(strip=True)

    # PATCH: skip BeautifulSoup entirely for plain posts (no blockquotes,
    # spoilers, code blocks, images, or links).  Only pay the BS4 cost when
    # the raw HTML actually contains markup we need to transform.
    blockquote_element_text = None
    replied_to = replied_to_message_date = replied_post_start = None
    post_description = ""

    if post_body_node:
        raw_html = post_body_node.html or ""

        has_blockquote = "blockquote" in raw_html
        needs_complex_parse = (
            has_blockquote
            or "spoiler" in raw_html
            or "codeblock" in raw_html
            or "<img" in raw_html
            or "<a " in raw_html
        )

        if needs_complex_parse:
            soup = BeautifulSoup(raw_html, 'lxml')

            if has_blockquote:
                first_bq = soup.find("blockquote")

                if first_bq:
                    cite = first_bq.find("cite")

                    if cite:
                        cite_text = cite.get_text(
                            separator=' ',
                            strip=True
                        )

                        m = REPLIED_TO_RE.search(cite_text)

                        if m:
                            replied_to = m.group(1).strip()

                        dm = REPLIED_TO_MESSAGE_DATE_RE.search(
                            cite_text
                        )

                        if dm:
                            replied_to_message_date = dm.group(1)

                    first_bq_for_matching = BeautifulSoup(
                        str(first_bq),
                        'lxml'
                    )

                    cite_el = first_bq_for_matching.find("cite")

                    if cite_el:
                        cite_el.decompose()

                    blockquote_element_text = POST_DESCRIPTION_RE.sub(
                        '',
                        first_bq_for_matching.get_text(separator='\n')
                    )

                    replied_post_start = get_text_start(
                        blockquote_element_text
                    )

            soup = process_post_body(soup)

            body = soup.find('body') or soup

            post_description = body.decode_contents().strip()

        else:
            # Fast path: no complex markup — store raw HTML as-is.
            # The frontend renders post_description as innerHTML so
            # block elements handle spacing naturally.
            post_description = raw_html

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
            'user_group': user_group,
            'user_stars': user_stars,
            'reputation': reputation,
            'replied_to': replied_to,
            'replied_to_post_date': replied_to_message_date,
            'post_description': post_description,
            'post_description_start': post_description_start,
            'replied_post_start': replied_post_start,
            'replies': []
        }
    )


def thread_parser(folder_path: str, debug: bool = False) -> Thread:
    if not os.path.exists(folder_path):
        raise FileNotFoundError(
            f"Target folder path context missing: {folder_path}"
        )

    pages = sorted(
        [
            f for f in os.listdir(folder_path)
            if f.endswith('.html')
        ],
        key=sortpages
    )

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

            for idx in [311, 315]:
                if idx < len(lines):
                    match = re.search(r'>(.*?)</a>', lines[idx])

                    if match:
                        thread_tree['categories'].append(
                            match.group(1).strip()
                        )

    posts_pos: Dict[Tuple[str, str], dict] = {}
    got_thread_content_info = False

    for page in pages:
        with open(
            os.path.join(folder_path, page),
            'r',
            encoding='utf-8'
        ) as f:
            content = f.read()

        tree = HTMLParser(content)

        posts = tree.css('.post')

        all_like_buttons = [
            el for el in tree.css('[id]')
            if LIKE_BUTTONS_RE.match(
                el.attributes.get('id', '')
            )
        ]

        if not got_thread_content_info and posts:
            title_node = tree.css_first('head title')

            thread_tree['title'] = (
                title_node.text().strip()
                if title_node else None
            )

            _, thread_content_info = get_post_information(
                posts[0],
                all_like_buttons,
                0,
                thread_tree['categories'],
                debug=debug,
                page=page
            )

            thread_tree['thread_content'] = thread_content_info

            p_date = thread_content_info['post_date']
            u_name = thread_content_info['author_username']
            p_start = thread_content_info['post_description_start']

            posts_pos[(p_date, u_name)] = thread_content_info
            posts_pos[(p_date, p_start)] = thread_content_info

        start_idx = 1 if not got_thread_content_info else 0

        for i in range(start_idx, len(posts)):
            blockquote_element_text, post_info = (
                get_post_information(
                    posts[i],
                    all_like_buttons,
                    i,
                    thread_tree['categories'],
                    debug=debug,
                    page=page
                )
            )

            p_date = post_info['post_date']
            u_name = post_info['author_username']
            p_start = post_info['post_description_start']

            posts_pos[(p_date, u_name)] = post_info
            posts_pos[(p_date, p_start)] = post_info

            r_date = post_info["replied_to_post_date"]
            r_to = post_info['replied_to']
            r_start = post_info['replied_post_start']

            if r_date and r_to:
                replied_post = (
                    posts_pos.get((r_date, r_to))
                    or posts_pos.get((r_date, r_start))
                )

                if replied_post:
                    replied_post['replies'].append(post_info)

                else:
                    thread_tree[
                        'replies_to_not_found_posts'
                    ].append(post_info)

                    if blockquote_element_text:
                        post_info['quote'] = (
                            blockquote_element_text
                        )

            else:
                if thread_tree['thread_content']:
                    thread_tree[
                        'thread_content'
                    ]['replies'].append(post_info)

        got_thread_content_info = True

    return thread_tree


def load_thread_json_meta(
    html_folder: str
) -> Tuple[
    Optional[str],
    Optional[str],
    Optional[str]
]:
    try:
        with open(
            os.path.join(html_folder, "thread.json"),
            "r",
            encoding="utf-8"
        ) as f:
            meta = json.load(f)

        author_info = meta.get("author") or {}

        return (
            (meta.get("title") or "").strip() or None,
            author_info.get("username") or None,
            author_info.get("id") or None,
        )

    except Exception:
        return None, None, None


def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=60)

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=200000")
    conn.execute("PRAGMA temp_store=MEMORY")

    conn.executescript('''
        CREATE TABLE IF NOT EXISTS threads (
            thread_id TEXT PRIMARY KEY,
            title TEXT,
            author_username TEXT,
            author_id TEXT,
            categories TEXT,
            date TEXT,
            reply_count INTEGER
        );

        CREATE TABLE IF NOT EXISTS posts (
            post_id TEXT PRIMARY KEY,
            thread_id TEXT,
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
            replied_to TEXT,
            replied_to_post_date TEXT,
            post_description TEXT,
            is_op INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_posts_thread_id
        ON posts(thread_id);
    ''')

    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS "
            "fts_posts USING fts5("
            "post_description, "
            "author_username, "
            "author_id, "
            "thread_id, "
            "post_id)"
        )

    except sqlite3.OperationalError:
        pass

    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS "
            "fts_threads USING fts5("
            "title, "
            "author_username, "
            "author_id, "
            "categories, "
            "thread_id)"
        )

    except sqlite3.OperationalError:
        pass

    return conn


def run_migration_on_file(
    thread_id: str,
    overwrite: bool = False
):
    html_folder = os.path.join(THREADS_DIR, thread_id)

    try:
        conn = init_db()
        cur = conn.cursor()

        if not overwrite:
            cur.execute(
                "SELECT thread_id FROM threads WHERE thread_id=?",
                (thread_id,)
            )

            if cur.fetchone():
                conn.close()
                return "skipped"

        data = thread_parser(html_folder)

        categories = (
            data.get("categories", [])
            or ["Uncategorized"]
        )

        meta_title, meta_username, meta_author_id = (
            load_thread_json_meta(html_folder)
        )

        clean_title = (
            meta_title
            or (data.get("title") or "Untitled Thread")
        ).strip()

        author = (
            meta_username
            or (
                data.get("thread_content") or {}
            ).get("author_username")
            or "Unknown User"
        )

        author_id = meta_author_id

        thread_content = data.get("thread_content") or {}

        thread_date = (
            thread_content.get("post_date")
            if thread_content else None
        )

        reply_count = (
            len(thread_content.get("replies", []))
            if thread_content else 0
        )

        cur.execute(
            """
            INSERT OR REPLACE INTO threads
            (
                thread_id,
                title,
                author_username,
                author_id,
                categories,
                date,
                reply_count
            )
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                thread_id,
                clean_title,
                author,
                author_id,
                json.dumps(categories),
                thread_date,
                reply_count
            )
        )

        def collect_posts(pinfo, is_op=False):
            out = [(pinfo, is_op)]

            for r in pinfo.get("replies", []):
                out.extend(collect_posts(r, False))

            return out

        for post_info, is_op in (
            collect_posts(thread_content, True)
            if thread_content else []
        ):
            post_id = (
                post_info.get("post_id")
                or f"{thread_id}_{post_info.get('post_number') or ''}".strip("_")
            )

            cur.execute(
                """
                INSERT OR REPLACE INTO posts(
                    post_id,
                    thread_id,
                    post_number,
                    unix_time,
                    post_date,
                    last_edit,
                    likes,
                    dislikes,
                    author_username,
                    author_id,
                    user_title,
                    user_rank,
                    user_group,
                    user_stars,
                    reputation,
                    replied_to,
                    replied_to_post_date,
                    post_description,
                    is_op
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
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
                    json.dumps(post_info.get('user_group')) if post_info.get('user_group') is not None else None,
                    post_info.get('user_stars'),
                    post_info.get('reputation'),
                    post_info.get('replied_to'),
                    post_info.get('replied_to_post_date'),
                    post_info.get('post_description'),
                    1 if is_op else 0
                )
            )

        fts_exists = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
            AND name='fts_posts'
            """
        ).fetchone()

        if fts_exists:
            update_fts_for_thread(conn, thread_id)

        conn.commit()
        conn.close()

        return True

    except Exception as e:
        return f"Error {thread_id}: {e}"


def update_fts_for_thread(
    conn: sqlite3.Connection,
    thread_id: str
):
    conn.execute(
        "DELETE FROM fts_threads WHERE thread_id = ?",
        (thread_id,)
    )

    conn.execute(
        """
        INSERT INTO fts_threads(
            title,
            author_username,
            author_id,
            categories,
            thread_id
        )
        SELECT
            title,
            author_username,
            author_id,
            categories,
            thread_id
        FROM threads
        WHERE thread_id = ?
        """,
        (thread_id,)
    )

    conn.execute(
        "DELETE FROM fts_posts WHERE thread_id = ?",
        (thread_id,)
    )

    conn.execute(
        """
        INSERT INTO fts_posts(
            post_description,
            author_username,
            author_id,
            thread_id,
            post_id
        )
        SELECT
            post_description,
            author_username,
            author_id,
            thread_id,
            post_id
        FROM posts
        WHERE thread_id = ?
        """,
        (thread_id,)
    )


def run_concurrent_migration(
    overwrite: bool = False,
    workers: int = 12
):
    os.makedirs(INDEX_DIR, exist_ok=True)

    all_thread_ids = [
        e.name for e in os.scandir(THREADS_DIR)
        if e.is_dir()
    ]

    total = len(all_thread_ids)

    print(
        f"Found {total:,} threads. "
        f"Starting with {workers} workers..."
    )

    print("Dropping FTS indexes for bulk insert...")

    conn = sqlite3.connect(DB_PATH, timeout=60)

    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute("DROP TABLE IF EXISTS fts_posts")
    conn.execute("DROP TABLE IF EXISTS fts_threads")

    conn.commit()
    conn.close()

    print("FTS indexes dropped.")

    chunk_size = 10000

    counts = {
        "done": 0,
        "skipped": 0,
        "failed": 0
    }

    with tqdm(
        total=total,
        desc="Threads",
        unit="thread",
        dynamic_ncols=True,
        mininterval=0.3,
        smoothing=0.1
    ) as pbar:

        for i in range(0, total, chunk_size):
            chunk = all_thread_ids[i:i + chunk_size]

            print(
                f"\nProcessing chunk "
                f"{i//chunk_size + 1} / "
                f"{total//chunk_size + 1} "
                f"({len(chunk):,} threads)..."
            )

            with ProcessPoolExecutor(
                max_workers=workers
            ) as executor:

                futures = {
                    executor.submit(
                        run_migration_on_file,
                        thread_id,
                        overwrite
                    ): thread_id
                    for thread_id in chunk
                }

                for future in as_completed(futures):
                    result = future.result()

                    if result is True:
                        counts["done"] += 1

                    elif result == "skipped":
                        counts["skipped"] += 1

                    else:
                        counts["failed"] += 1

                        if "Error" in str(result):
                            print(f"\n{result}")

                    pbar.update(1)

    print("\n" + "=" * 70)

    print(f"✅ Done:     {counts['done']:,}")
    print(f"⏭ Skipped:  {counts['skipped']:,}")
    print(f"❌ Failed:   {counts['failed']:,}")

    print("=" * 70)

    print("\nRebuilding FTS indexes...")

    conn = sqlite3.connect(DB_PATH, timeout=300)

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS fts_posts
        USING fts5(
            post_description,
            author_username,
            author_id,
            thread_id,
            post_id
        )
        """
    )

    print("  Populating fts_posts...")

    conn.execute("DELETE FROM fts_posts")

    conn.execute(
        """
        INSERT INTO fts_posts(
            post_description,
            author_username,
            author_id,
            thread_id,
            post_id
        )
        SELECT
            post_description,
            author_username,
            author_id,
            thread_id,
            post_id
        FROM posts
        """
    )

    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS fts_threads
        USING fts5(
            title,
            author_username,
            author_id,
            categories,
            thread_id
        )
        """
    )

    print("  Populating fts_threads...")

    conn.execute("DELETE FROM fts_threads")

    conn.execute(
        """
        INSERT INTO fts_threads(
            title,
            author_username,
            author_id,
            categories,
            thread_id
        )
        SELECT
            title,
            author_username,
            author_id,
            categories,
            thread_id
        FROM threads
        """
    )

    conn.commit()

    print("FTS indexes rebuilt.")

    print("Running VACUUM (this may take a few minutes)...")

    conn.execute("VACUUM")
    conn.close()

    print("VACUUM complete.")

    print("🎉 Migration finished!")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument("--thread", type=str, default=None)
    parser.add_argument("--overwrite", action="store_true")

    args = parser.parse_args()

    if args.thread:
        result = run_migration_on_file(
            args.thread,
            args.overwrite
        )

        print(f"Result: {result}")

    else:
        run_concurrent_migration(
            overwrite=args.overwrite,
            workers=12
        )