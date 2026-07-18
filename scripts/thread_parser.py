# Usage:
#   python scripts/thread_parser.py                               # all threads (skips already in DB)
#   python scripts/thread_parser.py --overwrite                   # force reprocess all
#   python scripts/thread_parser.py --thread 1218438              # single thread
#   python scripts/thread_parser.py --thread 1218438 --overwrite  # single thread, force

import re
import os
import json
import copy
import threading
import sys
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from selectolax.parser import HTMLParser, Node
from bs4 import BeautifulSoup
from typing import TypedDict, List, Dict, Tuple, Optional, Any
from tqdm import tqdm
from datetime import datetime
import sqlite3
from collections.abc import Callable

from v3rm_assets import (
    normalize_image_path,
    resolve_usergroup,
    split_glued_image_text,
)

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

# ── Paths ──────────────────────────────────────────────────
ROOT_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR    = os.path.join(ROOT_DIR, "data")
THREADS_DIR = os.path.join(DATA_DIR, "threads")
THREADS_ZIP = os.path.join(DATA_DIR, "threads.zip")
INDEX_DIR   = os.path.join(DATA_DIR, "index")
DB_PATH     = os.path.join(DATA_DIR, "threads.db")
DEFAULT_FOLDER_WORKERS = min(24, os.cpu_count() or 8)

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


def sortpages(v: str) -> int:
    match = PAGE_RE.search(v)
    return int(match.group()) if match else 0


def parse_user_stars(author_info_node) -> int:
    if not author_info_node:
        return 0
    return len(author_info_node.css('img[src*="star"]'))


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


def process_post_body(
    soup: BeautifulSoup,
    *,
    has_codeblock: bool,
    has_blockquote: bool,
    has_spoiler: bool,
    has_assets: bool,
) -> BeautifulSoup:
    if has_codeblock:
        soup = extract_code_blocks(soup)
    if has_blockquote:
        soup = transform_quotes(soup)
    if has_spoiler:
        soup = transform_spoilers(soup)
    if has_assets:
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
        has_spoiler = "spoiler" in raw_html
        has_codeblock = "codeblock" in raw_html
        has_assets = "<img" in raw_html or "<a " in raw_html
        needs_complex_parse = (
            has_blockquote
            or has_spoiler
            or has_codeblock
            or has_assets
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

                    first_bq_for_matching = copy.copy(first_bq)

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

            soup = process_post_body(
                soup,
                has_codeblock=has_codeblock,
                has_blockquote=has_blockquote,
                has_spoiler=has_spoiler,
                has_assets=has_assets,
            )

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


def _thread_parser(
    folder_path: str,
    pages: List[str],
    read_page: Callable[[str], str],
    debug: bool = False,
) -> Thread:
    thread_tree: Thread = {
        'thread_path': folder_path,
        'categories': [],
        'title': None,
        'thread_content': None,
        'replies_to_not_found_posts': []
    }

    first_page_content = None
    if pages:
        first_page_content = read_page(pages[0])
        lines = first_page_content.splitlines(keepends=True)

        for idx in [311, 315]:
            if idx < len(lines):
                match = re.search(r'>(.*?)</a>', lines[idx])

                if match:
                    thread_tree['categories'].append(
                        match.group(1).strip()
                    )

    posts_pos: Dict[Tuple[str, str], dict] = {}
    got_thread_content_info = False

    for page_index, page in enumerate(pages):
        content = (
            first_page_content
            if page_index == 0 and first_page_content is not None
            else read_page(page)
        )

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

    def read_page(page: str) -> str:
        with open(
            os.path.join(folder_path, page),
            'r',
            encoding='utf-8'
        ) as f:
            return f.read()

    return _thread_parser(folder_path, pages, read_page, debug)


def thread_parser_from_pages(
    folder_path: str,
    page_contents: Dict[str, str],
    debug: bool = False,
) -> Thread:
    pages = sorted(page_contents, key=sortpages)
    return _thread_parser(
        folder_path,
        pages,
        page_contents.__getitem__,
        debug,
    )


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
        return parse_thread_json_meta(meta)

    except Exception:
        return None, None, None


def parse_thread_json_meta(meta: dict) -> Tuple[
    Optional[str],
    Optional[str],
    Optional[str]
]:
    author_info = meta.get("author") or {}

    return (
        (meta.get("title") or "").strip() or None,
        author_info.get("username") or None,
        author_info.get("id") or None,
    )


def create_external_fts(conn):
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS fts_posts USING fts5("
        "post_description, author_username, author_id, thread_id, post_id, "
        "content='posts', content_rowid='rowid', columnsize=0)"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS fts_threads USING fts5("
        "title, author_username, author_id, categories, thread_id, "
        "content='threads', content_rowid='rowid', columnsize=0)"
    )


def drop_fts_triggers(conn):
    for name in (
        "posts_fts_ai", "posts_fts_ad", "posts_fts_au",
        "threads_fts_ai", "threads_fts_ad", "threads_fts_au",
    ):
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")


def create_fts_triggers(conn):
    drop_fts_triggers(conn)
    conn.executescript("""
        CREATE TRIGGER posts_fts_ai AFTER INSERT ON posts BEGIN
            INSERT INTO fts_posts(
                rowid, post_description, author_username,
                author_id, thread_id, post_id
            ) VALUES (
                new.rowid, new.post_description, new.author_username,
                new.author_id, new.thread_id, new.post_id
            );
        END;

        CREATE TRIGGER posts_fts_ad AFTER DELETE ON posts BEGIN
            INSERT INTO fts_posts(
                fts_posts, rowid, post_description, author_username,
                author_id, thread_id, post_id
            ) VALUES (
                'delete', old.rowid, old.post_description, old.author_username,
                old.author_id, old.thread_id, old.post_id
            );
        END;

        CREATE TRIGGER posts_fts_au AFTER UPDATE ON posts BEGIN
            INSERT INTO fts_posts(
                fts_posts, rowid, post_description, author_username,
                author_id, thread_id, post_id
            ) VALUES (
                'delete', old.rowid, old.post_description, old.author_username,
                old.author_id, old.thread_id, old.post_id
            );
            INSERT INTO fts_posts(
                rowid, post_description, author_username,
                author_id, thread_id, post_id
            ) VALUES (
                new.rowid, new.post_description, new.author_username,
                new.author_id, new.thread_id, new.post_id
            );
        END;

        CREATE TRIGGER threads_fts_ai AFTER INSERT ON threads BEGIN
            INSERT INTO fts_threads(
                rowid, title, author_username, author_id, categories, thread_id
            ) VALUES (
                new.rowid, new.title, new.author_username,
                new.author_id, new.categories, new.thread_id
            );
        END;

        CREATE TRIGGER threads_fts_ad AFTER DELETE ON threads BEGIN
            INSERT INTO fts_threads(
                fts_threads, rowid, title, author_username,
                author_id, categories, thread_id
            ) VALUES (
                'delete', old.rowid, old.title, old.author_username,
                old.author_id, old.categories, old.thread_id
            );
        END;

        CREATE TRIGGER threads_fts_au AFTER UPDATE ON threads BEGIN
            INSERT INTO fts_threads(
                fts_threads, rowid, title, author_username,
                author_id, categories, thread_id
            ) VALUES (
                'delete', old.rowid, old.title, old.author_username,
                old.author_id, old.categories, old.thread_id
            );
            INSERT INTO fts_threads(
                rowid, title, author_username, author_id, categories, thread_id
            ) VALUES (
                new.rowid, new.title, new.author_username,
                new.author_id, new.categories, new.thread_id
            );
        END;
    """)


def init_db(create_fts: bool = True):
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

    if create_fts:
        try:
            create_external_fts(conn)
            fts_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='fts_posts'"
            ).fetchone()
            if fts_sql and "content='posts'" in fts_sql[0]:
                create_fts_triggers(conn)
        except sqlite3.OperationalError:
            pass

    return conn


THREAD_INSERT_SQL = """
    INSERT INTO threads (
        thread_id,
        title,
        author_username,
        author_id,
        categories,
        date,
        reply_count
    )
    VALUES (?,?,?,?,?,?,?)
    ON CONFLICT(thread_id) DO UPDATE SET
        title=excluded.title,
        author_username=excluded.author_username,
        author_id=excluded.author_id,
        categories=excluded.categories,
        date=excluded.date,
        reply_count=excluded.reply_count
"""


POST_INSERT_SQL = """
    INSERT INTO posts (
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
    ON CONFLICT(post_id) DO UPDATE SET
        thread_id=excluded.thread_id,
        post_number=excluded.post_number,
        unix_time=excluded.unix_time,
        post_date=excluded.post_date,
        last_edit=excluded.last_edit,
        likes=excluded.likes,
        dislikes=excluded.dislikes,
        author_username=excluded.author_username,
        author_id=excluded.author_id,
        user_title=excluded.user_title,
        user_rank=excluded.user_rank,
        user_group=excluded.user_group,
        user_stars=excluded.user_stars,
        reputation=excluded.reputation,
        replied_to=excluded.replied_to,
        replied_to_post_date=excluded.replied_to_post_date,
        post_description=excluded.post_description,
        is_op=excluded.is_op
"""


def collect_posts(pinfo, is_op=False):
    out = [(pinfo, is_op)]

    for reply in pinfo.get("replies", []):
        out.extend(collect_posts(reply, False))

    return out


def _build_thread_rows(
    thread_id: str,
    data: Thread,
    thread_meta: Tuple[Optional[str], Optional[str], Optional[str]],
):
    categories = data.get("categories", []) or ["Uncategorized"]
    meta_title, meta_username, meta_author_id = thread_meta
    thread_content = data.get("thread_content") or {}

    clean_title = (
        meta_title
        or (data.get("title") or "Untitled Thread")
    ).strip()
    author = (
        meta_username
        or thread_content.get("author_username")
        or "Unknown User"
    )
    thread_date = thread_content.get("post_date") if thread_content else None
    reply_count = (
        len(thread_content.get("replies", []))
        if thread_content else 0
    )
    thread_row = (
        thread_id,
        clean_title,
        author,
        meta_author_id,
        json.dumps(categories),
        thread_date,
        reply_count,
    )

    post_rows = []
    for post_info, is_op in (
        collect_posts(thread_content, True) if thread_content else []
    ):
        post_id = (
            post_info.get("post_id")
            or f"{thread_id}_{post_info.get('post_number') or ''}".strip("_")
        )
        post_rows.append((
            post_id,
            thread_id,
            post_info.get("post_number"),
            post_info.get("unix_time"),
            post_info.get("post_date"),
            post_info.get("last_edit"),
            post_info.get("likes"),
            post_info.get("dislikes"),
            post_info.get("author_username"),
            post_info.get("author_id"),
            post_info.get("user_title"),
            post_info.get("user_rank"),
            (
                json.dumps(post_info.get("user_group"))
                if post_info.get("user_group") is not None
                else None
            ),
            post_info.get("user_stars"),
            post_info.get("reputation"),
            post_info.get("replied_to"),
            post_info.get("replied_to_post_date"),
            post_info.get("post_description"),
            1 if is_op else 0,
        ))

    return thread_row, post_rows


def build_thread_rows(thread_id: str):
    html_folder = os.path.join(THREADS_DIR, thread_id)
    return _build_thread_rows(
        thread_id,
        thread_parser(html_folder),
        load_thread_json_meta(html_folder),
    )


def build_thread_rows_from_pages(
    thread_id: str,
    page_contents: Dict[str, str],
    metadata_content: str | None,
):
    folder_path = os.path.join(THREADS_DIR, thread_id)
    try:
        thread_meta = parse_thread_json_meta(
            json.loads(metadata_content) if metadata_content else {}
        )
    except Exception:
        thread_meta = (None, None, None)

    return _build_thread_rows(
        thread_id,
        thread_parser_from_pages(folder_path, page_contents),
        thread_meta,
    )


def parse_thread_worker(thread_id: str):
    try:
        return "success", build_thread_rows(thread_id)
    except Exception as exc:
        return "error", f"Error {thread_id}: {exc}"


def write_thread_batch(cur, thread_rows, post_rows):
    if thread_rows:
        cur.executemany(THREAD_INSERT_SQL, thread_rows)
    if post_rows:
        cur.executemany(POST_INSERT_SQL, post_rows)


def run_migration_on_file(
    thread_id: str,
    overwrite: bool = False
):
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

        thread_row, post_rows = build_thread_rows(thread_id)
        write_thread_batch(cur, [thread_row], post_rows)

        fts_schema = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type='table'
            AND name='fts_posts'
            """
        ).fetchone()

        if fts_schema and "content='posts'" not in fts_schema[0]:
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


def load_thread_archive_index():
    archive = zipfile.ZipFile(THREADS_ZIP)
    entries = {}

    for info in archive.infolist():
        parts = info.filename.split("/")
        if len(parts) < 2 or parts[0] != "threads" or not parts[1]:
            continue

        thread_id = parts[1]
        thread_entries = entries.setdefault(thread_id, [])
        if not info.is_dir() and (
            info.filename.endswith(".html")
            or info.filename.endswith("/thread.json")
        ):
            thread_entries.append(info)

    return archive, entries


def build_thread_rows_from_archive(archive, thread_id, entries):
    pages = {}
    metadata = None

    for info in entries:
        name = info.filename.rsplit("/", 1)[-1]
        content = archive.read(info).decode("utf-8")

        if name.endswith(".html"):
            pages[name] = content
        elif name == "thread.json":
            metadata = content

    return build_thread_rows_from_pages(
        thread_id,
        pages,
        metadata,
    )


def run_concurrent_migration(
    overwrite: bool = False,
    workers: int = DEFAULT_FOLDER_WORKERS,
    source: str = "auto",
):
    os.makedirs(INDEX_DIR, exist_ok=True)

    if source == "auto":
        source = "zip" if os.path.isfile(THREADS_ZIP) else "folders"

    archive = None
    archive_entries = None
    if source == "zip":
        if not os.path.isfile(THREADS_ZIP):
            raise FileNotFoundError(f"Thread archive not found: {THREADS_ZIP}")
        print("Loading thread ZIP directory...")
        archive, archive_entries = load_thread_archive_index()
        all_thread_ids = list(archive_entries)
    elif source == "folders":
        all_thread_ids = [
            e.name for e in os.scandir(THREADS_DIR)
            if e.is_dir()
        ]
    else:
        raise ValueError(f"Unknown thread source: {source}")

    total = len(all_thread_ids)

    if source == "zip":
        print(f"Found {total:,} threads in ZIP. Starting sequential parse...")
    else:
        print(
            f"Found {total:,} threads. "
            f"Starting with {workers} workers..."
        )

    write_conn = init_db(create_fts=False)
    write_conn.execute("PRAGMA synchronous=NORMAL")

    if overwrite:
        thread_ids = all_thread_ids
        skipped_count = 0
    else:
        print("Loading existing thread IDs once...")
        existing_ids = {
            row[0]
            for row in write_conn.execute("SELECT thread_id FROM threads")
        }
        thread_ids = [
            thread_id for thread_id in all_thread_ids
            if thread_id not in existing_ids
        ]
        skipped_count = total - len(thread_ids)
        print(f"Skipping {skipped_count:,} existing threads.")

    if not thread_ids:
        write_conn.close()
        if archive:
            archive.close()
        print("No threads need processing.")
        return

    print("Dropping FTS indexes for bulk insert...")
    drop_fts_triggers(write_conn)
    write_conn.execute("DROP TABLE IF EXISTS fts_posts")
    write_conn.execute("DROP TABLE IF EXISTS fts_threads")
    write_conn.commit()
    print("FTS indexes dropped; they will remain off during parsing.")

    chunk_size = 10000
    write_batch_size = 500

    counts = {
        "done": 0,
        "skipped": skipped_count,
        "failed": 0
    }

    write_cur = write_conn.cursor()
    pending_threads = []
    pending_posts = []

    def flush_pending():
        if not pending_threads:
            return
        write_thread_batch(write_cur, pending_threads, pending_posts)
        write_conn.commit()
        pending_threads.clear()
        pending_posts.clear()

    with tqdm(
        total=total,
        desc="Threads",
        unit="thread",
        dynamic_ncols=True,
        mininterval=0.3,
        smoothing=0.1
    ) as pbar:
        if skipped_count:
            pbar.update(skipped_count)

        def handle_result(status, payload):
            if status == "success":
                thread_row, post_rows = payload
                pending_threads.append(thread_row)
                pending_posts.extend(post_rows)
                counts["done"] += 1

                if len(pending_threads) >= write_batch_size:
                    flush_pending()
            else:
                counts["failed"] += 1
                print(f"\n{payload}")

            pbar.update(1)

        if source == "zip":
            assert archive is not None
            assert archive_entries is not None
            for thread_id in thread_ids:
                try:
                    payload = build_thread_rows_from_archive(
                        archive,
                        thread_id,
                        archive_entries[thread_id],
                    )
                    handle_result("success", payload)
                except Exception as exc:
                    handle_result(
                        "error",
                        f"Error {thread_id}: {exc}",
                    )
        else:
            with ProcessPoolExecutor(max_workers=workers) as executor:
                for i in range(0, len(thread_ids), chunk_size):
                    chunk = thread_ids[i:i + chunk_size]

                    print(
                        f"\nProcessing chunk "
                        f"{i//chunk_size + 1} / "
                        f"{(len(thread_ids) - 1)//chunk_size + 1} "
                        f"({len(chunk):,} threads)..."
                    )

                    futures = {
                        executor.submit(
                            parse_thread_worker,
                            thread_id
                        ): thread_id
                        for thread_id in chunk
                    }

                    for future in as_completed(futures):
                        handle_result(*future.result())

                    flush_pending()

    flush_pending()
    write_conn.close()
    if archive:
        archive.close()

    print("\n" + "=" * 70)

    print(f"✅ Done:     {counts['done']:,}")
    print(f"⏭ Skipped:  {counts['skipped']:,}")
    print(f"❌ Failed:   {counts['failed']:,}")

    print("=" * 70)

    print("\nRebuilding FTS indexes...")

    conn = sqlite3.connect(DB_PATH, timeout=300)

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    create_external_fts(conn)

    print("  Populating fts_posts...")
    conn.execute("INSERT INTO fts_posts(fts_posts) VALUES('rebuild')")

    print("  Populating fts_threads...")
    conn.execute("INSERT INTO fts_threads(fts_threads) VALUES('rebuild')")
    conn.execute("INSERT INTO fts_posts(fts_posts) VALUES('optimize')")
    conn.execute("INSERT INTO fts_threads(fts_threads) VALUES('optimize')")
    create_fts_triggers(conn)

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
    parser.add_argument("--workers", type=int, default=DEFAULT_FOLDER_WORKERS)
    parser.add_argument(
        "--source",
        choices=("auto", "zip", "folders"),
        default="auto",
        help="Bulk input source; auto prefers data/threads.zip",
    )

    args = parser.parse_args()

    if args.thread:
        result = run_migration_on_file(
            args.thread,
            args.overwrite
        )

        print(f"Result: {result}")

    else:
        if args.workers < 1:
            parser.error("--workers must be at least 1")
        run_concurrent_migration(
            overwrite=args.overwrite,
            workers=args.workers,
            source=args.source,
        )
