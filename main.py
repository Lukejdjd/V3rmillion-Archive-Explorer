import os
import sys
import signal
import subprocess
import threading
import http.server
import socketserver
import urllib.request
import urllib.error
import json

# ── Config ────────────────────────────────────────────────────────────────────
HOST = os.environ.get("HOST", "localhost")
PORT = int(os.environ.get("PORT", "8000"))
SERVE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("ARCHIVE_DATA_DIR", os.path.join(SERVE_DIR, "data"))
IFRAMELY_BACKEND = os.environ.get(
    "IFRAMELY_BACKEND", "http://localhost:8061"
)
MANAGE_IFRAMELY = os.environ.get("MANAGE_IFRAMELY", "1").lower() not in {
    "0", "false", "no"
}

# ── Handler ───────────────────────────────────────────────────────────────────
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SERVE_DIR, **kwargs)

    def do_GET(self):
        if self.path.startswith('/api/'):
            return self.handle_api()
        if self.path.startswith('/member.php'):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            uid = (qs.get('uid') or [''])[0]
            if uid and (qs.get('action') or [''])[0] == 'profile':
                self.send_response(302)
                self.send_header('Location', f'/static/search.html?uid={uid}')
                self.end_headers()
                return
        if self.path.startswith('/showthread.php'):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            thread_id = (qs.get('thread_id') or [''])[0]
            if thread_id:
                self.send_response(302)
                self.send_header('Location', f'/static/search.html?id={thread_id}')
                self.end_headers()
                return
        if self.path.startswith('/iframely'):
            self.proxy_to_iframely()
        else:
            try:
                super().do_GET()
            except FileNotFoundError:
                try:
                    self.send_response(404)
                    self.send_header('Content-Type', 'text/plain; charset=utf-8')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
            except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                print(f"Unhandled error serving static file: {e}")
                try:
                    self.send_response(500)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

    def proxy_to_iframely(self):
        target_url = f"{IFRAMELY_BACKEND}{self.path}"
        print(f"Proxying to Iframely: {target_url}")
        try:
            req = urllib.request.Request(target_url)
            req.add_header('User-Agent', 'Mozilla/5.0 Archive-Proxy')
            with urllib.request.urlopen(req, timeout=8) as response:
                data = response.read()
                self.send_response(response.status)
                if response.headers.get('Content-Type'):
                    self.send_header('Content-Type', response.headers.get('Content-Type'))
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            error_data = e.read().decode('utf-8', errors='ignore')
            print(f"Iframely container returned HTTP {e.code}: {error_data}")
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(error_data.encode('utf-8'))
        except Exception as e:
            print(f"Proxy error: {str(e)}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Proxy error: {str(e)}".encode('utf-8'))

    def handle_api(self):
        import urllib.parse, sqlite3

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        data_dir = DATA_DIR
        threads_db = os.path.join(data_dir, 'threads.db')
        users_db = os.path.join(data_dir, 'users.db')

        def connect_archive(path):
            absolute = os.path.abspath(path).replace('\\', '/')
            uri_path = urllib.parse.quote(absolute, safe='/:')
            conn = sqlite3.connect(
                f"file:{uri_path}?mode=ro&immutable=1",
                uri=True,
            )
            conn.execute("PRAGMA query_only=ON")
            return conn

        def send_json(obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _parse_awards_field(val):
            if val is None or val == '': return []
            if isinstance(val, (list, tuple)): return list(val)
            try:
                parsed = json.loads(val)
                return parsed if isinstance(parsed, list) else []
            except:
                return []

        def _parse_json_field(val):
            if val is None or val == '': return None
            try: return json.loads(val)
            except: return None

        def _bounded_int(name, default, minimum=0, maximum=1000):
            try:
                value = int((qs.get(name) or [default])[0])
            except (TypeError, ValueError):
                value = default
            return max(minimum, min(value, maximum))

        try:
            # ── /api/search_posts ────────────────────────────────────────────
            if path == '/api/search_posts':
                q         = (qs.get('q')         or [''])[0].strip()
                uid_input = (qs.get('uid')        or [None])[0]
                thread_id = (qs.get('thread_id')  or [None])[0]
                start_ts  = (qs.get('start_ts')   or [None])[0]
                end_ts    = (qs.get('end_ts')      or [None])[0]
                sort      = (qs.get('sort')        or ['desc'])[0]
                offset    = _bounded_int('offset', 0, 0, 100000000)
                limit     = _bounded_int('limit', 50, 1, 100)
                op_only   = (qs.get('op_only')    or ['0'])[0] == '1'

                try: start_ts = int(start_ts) if start_ts not in (None, '') else None
                except: start_ts = None
                try: end_ts = int(end_ts) if end_ts not in (None, '') else None
                except: end_ts = None

                if not os.path.exists(threads_db):
                    return send_json({'error': 'threads.db not found'}, 500)

                conn = connect_archive(threads_db)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    "SELECT sql FROM sqlite_master WHERE name='fts_posts'"
                )
                fts_posts_schema = cur.fetchone()
                fts_posts_external = bool(
                    fts_posts_schema
                    and "content='posts'" in fts_posts_schema[0]
                )
                fts_posts_join = (
                    "p.rowid = f.rowid" if fts_posts_external
                    else "p.post_id = f.post_id"
                )

                # Use post-time user fields stored on the post row itself.
                # Do NOT join users.db for user_title/user_rank/user_stars/user_group —
                # those reflect the user's rank at the time the post was made.
                select_fields = "p.*, t.title AS thread_title"
                left_join_user = ""

                def extra_filters():
                    sql, params = '', []
                    if thread_id:
                        sql += " AND p.thread_id = ?"
                        params.append(thread_id)
                    if start_ts is not None:
                        sql += " AND CAST(p.unix_time AS INTEGER) >= ?"
                        params.append(start_ts)
                    if end_ts is not None:
                        sql += " AND CAST(p.unix_time AS INTEGER) <= ?"
                        params.append(end_ts)
                    if op_only:
                        sql += " AND p.is_op = 1"
                    return sql, params

                def order_clause():
                    if sort == 'likes_desc':
                        return " ORDER BY CAST(COALESCE(NULLIF(p.likes, ''), '0') AS INTEGER) DESC, CAST(p.unix_time AS INTEGER) DESC"
                    if sort == 'likes_asc':
                        return " ORDER BY CAST(COALESCE(NULLIF(p.likes, ''), '0') AS INTEGER) ASC, CAST(p.unix_time AS INTEGER) DESC"
                    if sort == 'asc':
                        return " ORDER BY CAST(p.unix_time AS INTEGER) ASC, p.post_number ASC"
                    return " ORDER BY CAST(p.unix_time AS INTEGER) DESC, p.post_number DESC"

                extra_sql, extra_params = extra_filters()
                rows = []

                uid_is_numeric = uid_input and (
                    str(uid_input).isdigit() or str(uid_input).startswith('pid_')
                )

                if q:
                    sanitized_q = '"' + q.replace('"', '""') + '"'
                    fts_where   = "fts_posts MATCH ?"
                    fts_params  = [sanitized_q]

                    if uid_input and not uid_is_numeric:
                        fts_where  = "fts_posts MATCH ? AND f.author_username = ?"
                        fts_params = [sanitized_q, uid_input]
                    elif uid_input and uid_is_numeric:
                        extra_sql   += " AND p.author_id = ?"
                        extra_params.append(uid_input)

                    try:
                        sql = (
                            f"SELECT {select_fields} FROM fts_posts f "
                            f"JOIN posts p ON {fts_posts_join} "
                            "LEFT JOIN threads t ON t.thread_id = p.thread_id "
                            f"{left_join_user} WHERE {fts_where}"
                        ) + extra_sql
                        params = fts_params + extra_params
                        if sort != 'relevance':
                            sql += order_clause()
                        sql += " LIMIT ? OFFSET ?"
                        params.extend([limit, offset])
                        cur.execute(sql, params)
                        rows = [dict(r) for r in cur.fetchall()]
                    except Exception as db_err:
                        print(f"FTS search_posts failed ({db_err}), falling back to LIKE.")

                    if not rows:
                        fb_sql = (
                            f"SELECT {select_fields} FROM posts p "
                            f"LEFT JOIN threads t ON t.thread_id = p.thread_id "
                            f"{left_join_user} WHERE p.post_description LIKE ?"
                        )
                        fb_params = [f'%{q}%']
                        if uid_input and not uid_is_numeric:
                            fb_sql    += " AND p.author_username = ?"
                            fb_params.append(uid_input)
                        elif uid_input and uid_is_numeric:
                            fb_sql    += " AND p.author_id = ?"
                            fb_params.append(uid_input)
                        fb_sql += extra_sql
                        fb_params.extend(extra_params)
                        fb_sql += order_clause() + " LIMIT ? OFFSET ?"
                        fb_params.extend([limit, offset])
                        cur.execute(fb_sql, fb_params)
                        rows = [dict(r) for r in cur.fetchall()]

                elif uid_input and not uid_is_numeric:
                    try:
                        sql = (
                            f"SELECT {select_fields} FROM fts_posts f "
                            f"JOIN posts p ON {fts_posts_join} "
                            "LEFT JOIN threads t ON t.thread_id = p.thread_id "
                            f"{left_join_user} WHERE f.author_username = ?"
                        ) + extra_sql + order_clause() + " LIMIT ? OFFSET ?"
                        params = [uid_input] + extra_params + [limit, offset]
                        cur.execute(sql, params)
                        rows = [dict(r) for r in cur.fetchall()]
                    except Exception as db_err:
                        print(f"FTS username lookup failed ({db_err}), falling back.")
                        sql = (
                            f"SELECT {select_fields} FROM posts p "
                            f"LEFT JOIN threads t ON t.thread_id = p.thread_id "
                            f"{left_join_user} WHERE p.author_username = ?"
                        ) + extra_sql + order_clause() + " LIMIT ? OFFSET ?"
                        params = [uid_input] + extra_params + [limit, offset]
                        cur.execute(sql, params)
                        rows = [dict(r) for r in cur.fetchall()]

                else:
                    if uid_input and uid_is_numeric:
                        extra_sql    = " AND p.author_id = ?" + extra_sql
                        extra_params = [uid_input] + extra_params
                    sql = (
                        f"SELECT {select_fields} FROM posts p "
                        f"LEFT JOIN threads t ON t.thread_id = p.thread_id "
                        f"{left_join_user} WHERE 1=1"
                    ) + extra_sql + order_clause() + " LIMIT ? OFFSET ?"
                    params = extra_params + [limit, offset]
                    cur.execute(sql, params)
                    rows = [dict(r) for r in cur.fetchall()]

                for r in rows:
                    r['awards'] = _parse_awards_field(r.get('awards'))
                    if 'user_group' in r:
                        r['user_group'] = _parse_json_field(r.get('user_group'))

                conn.close()
                return send_json({'results': rows})

            # ── /api/user_posts ──────────────────────────────────────────────
            if path == '/api/user_posts':
                uid = (qs.get('uid') or [''])[0]
                if not uid:
                    return send_json({'error': 'uid required'}, 400)
                if not os.path.exists(threads_db):
                    return send_json({'error': 'threads.db not found'}, 500)

                op_only = (qs.get('op_only') or ['0'])[0] == '1'
                offset  = _bounded_int('offset', 0, 0, 100000000)
                limit   = _bounded_int('limit', 50, 1, 100)

                conn = connect_archive(threads_db)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()

                # Use post-time user fields stored on the post row itself.
                select_fields = "p.*, t.title AS thread_title"
                left_join_user = ""

                op_filter = "AND p.is_op = 1" if op_only else ""
                sql = f"""SELECT {select_fields} FROM posts p
                          LEFT JOIN threads t ON t.thread_id = p.thread_id
                          {left_join_user}
                          WHERE p.author_id = ? {op_filter}
                          ORDER BY CAST(p.unix_time AS INTEGER) DESC, p.post_number DESC
                          LIMIT ? OFFSET ?"""
                
                cur.execute(sql, (uid, limit, offset))
                posts = [dict(r) for r in cur.fetchall()]
                for p in posts:
                    p['awards'] = _parse_awards_field(p.get('awards'))
                    if 'user_group' in p:
                        p['user_group'] = _parse_json_field(p.get('user_group'))
                conn.close()
                return send_json({'posts': posts})

            # ── /api/user ────────────────────────────────────────────────────
            if path == '/api/user':
                uid = (qs.get('uid') or [''])[0]
                if not uid:
                    return send_json({'error': 'uid required'}, 400)
                if not os.path.exists(users_db):
                    return send_json({'error': 'users.db not found'}, 500)

                conn = connect_archive(users_db)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute('PRAGMA table_info(users)')
                cols = {row['name'] for row in cur.fetchall()}
                uid_col = 'user_id' if 'user_id' in cols else 'uid'

                cur.execute(f'SELECT * FROM users WHERE {uid_col} = ?', (uid,))
                row = cur.fetchone()
                conn.close()

                if not row:
                    return send_json({'error': 'user not found'}, 404)

                user = dict(row)
                user.pop('past_usernames_search', None)
                user['awards'] = _parse_awards_field(user.get('awards'))
                if 'user_group' in user:
                    user['user_group'] = _parse_json_field(user.get('user_group'))
                if 'past_usernames' in user:
                    user['past_usernames'] = _parse_json_field(user.get('past_usernames')) or []
                return send_json({'user': user})

            # ── /api/thread ──────────────────────────────────────────────────
            if path == '/api/thread':
                thread_id = (qs.get('thread_id') or [''])[0]
                requested_limit = (qs.get('limit') or [None])[0]
                limit = _bounded_int('limit', 50, 1, 100) if requested_limit is not None else None
                offset = _bounded_int('offset', 0, 0, 100000000)
                jump_post_id = (qs.get('post_id') or [None])[0]
                if not thread_id:
                    return send_json({'error': 'thread_id required'}, 400)
                if not os.path.exists(threads_db):
                    return send_json({'error': 'threads.db not found'}, 500)

                conn = connect_archive(threads_db)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute('SELECT * FROM threads WHERE thread_id = ?', (thread_id,))
                thread = cur.fetchone()
                if not thread:
                    conn.close()
                    return send_json({'error': 'not found'}, 404)

                # Use post-time user fields stored on the post row itself.
                select_fields = "p.*"
                left_join_user = ""

                cur.execute('SELECT COUNT(*) FROM posts WHERE thread_id = ?', (thread_id,))
                total_posts = cur.fetchone()[0]

                if limit is not None and jump_post_id:
                    cur.execute(
                        """SELECT row_number FROM (
                               SELECT post_id, ROW_NUMBER() OVER (
                                   ORDER BY CASE WHEN unix_time IS NULL OR unix_time = '' THEN 1 ELSE 0 END,
                                            CAST(unix_time AS INTEGER) ASC, post_number ASC
                               ) AS row_number
                               FROM posts WHERE thread_id = ?
                           ) WHERE post_id = ?""",
                        (thread_id, jump_post_id),
                    )
                    jump_row = cur.fetchone()
                    if jump_row:
                        offset = ((jump_row[0] - 1) // limit) * limit

                sql = f"""SELECT {select_fields} FROM posts p
                          {left_join_user}
                          WHERE p.thread_id = ?
                          ORDER BY CASE WHEN unix_time IS NULL OR unix_time = "" THEN 1 ELSE 0 END,
                          CAST(unix_time AS INTEGER) ASC, post_number ASC"""

                params = [thread_id]
                if limit is not None:
                    sql += " LIMIT ? OFFSET ?"
                    params.extend([limit, offset])
                cur.execute(sql, params)
                posts = [dict(r) for r in cur.fetchall()]
                for p in posts:
                    p['awards'] = _parse_awards_field(p.get('awards'))
                    if 'user_group' in p:
                        p['user_group'] = _parse_json_field(p.get('user_group'))
                conn.close()
                return send_json({
                    'thread': dict(thread),
                    'posts': posts,
                    'total': total_posts,
                    'offset': offset,
                    'limit': limit or total_posts,
                })

            # ── /api/search_threads ──────────────────────────────────────────
            if path == '/api/search_threads':
                q       = (qs.get('q')      or [''])[0].strip()
                cat     = (qs.get('cat')    or [''])[0].strip()
                author  = (qs.get('author') or [''])[0].strip()
                sort    = (qs.get('sort')   or ['relevance'])[0]
                order   = (qs.get('order')  or ['desc'])[0]
                offset  = _bounded_int('offset', 0, 0, 100000000)
                limit   = _bounded_int('limit', 24, 1, 100)

                if not os.path.exists(threads_db):
                    return send_json({'error': 'threads.db not found'}, 500)

                conn = connect_archive(threads_db)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()

                cur.execute(
                    "SELECT sql FROM sqlite_master WHERE name='fts_threads'"
                )
                fts_schema = cur.fetchone()
                has_fts = fts_schema is not None
                fts_join = (
                    "t.rowid = f.rowid"
                    if fts_schema and "content='threads'" in fts_schema[0]
                    else "t.thread_id = f.thread_id"
                )

                dir_sql = 'ASC' if order == 'asc' else 'DESC'

                if sort == 'relevance' and not q:
                    sort = 'thread_id'

                params = []
                sort_by_likes = sort == 'likes'

                if has_fts and q:
                    sanitized_q = '"' + q.replace('"', '""') + '"'
                    like_select = (
                        "COALESCE(MAX(CAST(NULLIF(p.likes, '') AS INTEGER)), 0)"
                        if sort_by_likes else "0"
                    )
                    base = (
                        "SELECT t.thread_id, t.title, t.author_username, t.author_id, "
                        "t.categories, t.date, t.reply_count, " + like_select + " AS like_count "
                        f"FROM fts_threads f JOIN threads t ON {fts_join} "
                    )
                    if sort_by_likes:
                        base += "LEFT JOIN posts p ON p.thread_id = t.thread_id "
                    base += "WHERE fts_threads MATCH ?"
                    params.append(sanitized_q)
                else:
                    like_select = (
                        "(SELECT COALESCE(MAX(CAST(NULLIF(p.likes, '') AS INTEGER)), 0) "
                        "FROM posts p WHERE p.thread_id = threads.thread_id)"
                        if sort_by_likes else "0"
                    )
                    base = (
                        "SELECT thread_id, title, author_username, author_id, "
                        "categories, date, reply_count, " + like_select + " AS like_count "
                        "FROM threads WHERE 1=1"
                    )

                if cat:
                    base += " AND t.categories LIKE ?" if (has_fts and q) else " AND categories LIKE ?"
                    params.append(f'%{cat}%')

                if author:
                    col = 'f.author_username' if (has_fts and q) else 'author_username'
                    base += f" AND {col} = ?"
                    params.append(author)

                if has_fts and q and sort_by_likes:
                    base += " GROUP BY t.thread_id"

                count_base = base

                if sort == 'relevance' and q and has_fts:
                    pass
                elif sort == 'thread_id':
                    base += f" ORDER BY CAST({'t.' if (has_fts and q) else ''}thread_id AS INTEGER) {dir_sql}"
                elif sort == 'title':
                    base += f" ORDER BY {'t.' if (has_fts and q) else ''}title {dir_sql}"
                elif sort == 'date':
                    base += f" ORDER BY {'t.' if (has_fts and q) else ''}date {dir_sql}"
                elif sort == 'replies':
                    base += f" ORDER BY {'t.' if (has_fts and q) else ''}reply_count {dir_sql}"
                elif sort == 'likes':
                    base += f" ORDER BY like_count {dir_sql}"

                count_sql = f"SELECT COUNT(*) FROM ({count_base})"
                try:
                    cur.execute(count_sql, params)
                    total = cur.fetchone()[0]
                except Exception:
                    total = None

                base += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                try:
                    cur.execute(base, params)
                    rows = cur.fetchall()
                except Exception as e:
                    conn.close()
                    return send_json({'error': str(e)}, 500)

                like_counts = {}
                if rows and not sort_by_likes:
                    thread_ids = [str(r['thread_id']) for r in rows]
                    placeholders = ','.join('?' for _ in thread_ids)
                    cur.execute(
                        "SELECT thread_id, COALESCE(MAX(CAST(NULLIF(likes, '') AS INTEGER)), 0) "
                        f"FROM posts WHERE thread_id IN ({placeholders}) GROUP BY thread_id",
                        thread_ids,
                    )
                    like_counts = {str(row[0]): row[1] for row in cur.fetchall()}

                out = []
                for r in rows:
                    try: cats = json.loads(r['categories']) if r['categories'] else []
                    except Exception: cats = []
                    out.append({
                        'thread_id':       str(r['thread_id']),
                        'title':           r['title'] or 'Untitled Thread',
                        'author':          r['author_username'] or '',
                        'author_id':       r['author_id'] or '',
                        'categories':      cats,
                        'date':            r['date'] or '',
                        'reply_count':     r['reply_count'],
                        'like_count':      like_counts.get(str(r['thread_id']), r['like_count']),
                    })

                conn.close()
                return send_json({'results': out, 'total': total, 'offset': offset, 'limit': limit})

            # ── /api/titles ──────────────────────────────────────────────────
            if path == '/api/titles':
                if not os.path.exists(threads_db):
                    return send_json({'error': 'threads.db not found'}, 500)
                conn = connect_archive(threads_db)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute('SELECT COUNT(*) as cnt FROM threads')
                row = cur.fetchone()
                conn.close()
                return send_json({'thread_count': row['cnt'] if row else 0})

            # ── /api/usergroups ──────────────────────────────────────────────
            if path == '/api/usergroups':
                index_dir = os.path.join(data_dir, 'index')
                fpath = os.path.join(index_dir, 'usergroups.json')
                if not os.path.exists(fpath):
                    return send_json({'error': 'usergroups.json not found'}, 404)
                with open(fpath, 'r', encoding='utf-8') as f:
                    return send_json(json.load(f))

            # ── /api/awards ──────────────────────────────────────────────────
            if path == '/api/awards':
                index_dir = os.path.join(data_dir, 'index')
                fpath = os.path.join(index_dir, 'awards.json')
                if not os.path.exists(fpath):
                    return send_json({'error': 'awards.json not found'}, 404)
                with open(fpath, 'r', encoding='utf-8') as f:
                    return send_json(json.load(f))

            # ── /api/smilies ─────────────────────────────────────────────────
            if path == '/api/smilies':
                index_dir = os.path.join(data_dir, 'index')
                fpath = os.path.join(index_dir, 'smilies.json')
                if not os.path.exists(fpath):
                    return send_json({'error': 'smilies.json not found'}, 404)
                with open(fpath, 'r', encoding='utf-8') as f:
                    return send_json(json.load(f))

            # ── /api/search_users ────────────────────────────────────────────
            if path == '/api/search_users':
                q      = (qs.get('q')      or [''])[0].strip()
                sort   = (qs.get('sort')   or ['relevance'])[0]
                order  = (qs.get('order')  or ['desc'])[0]
                offset = _bounded_int('offset', 0, 0, 100000000)
                limit  = _bounded_int('limit', 24, 1, 100)

                if not os.path.exists(users_db):
                    return send_json({'error': 'users.db not found'}, 500)

                conn = connect_archive(users_db)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()

                cur.execute(
                    "SELECT sql FROM sqlite_master WHERE name='fts_users'"
                )
                fts_schema = cur.fetchone()
                has_fts = fts_schema is not None
                fts_join = (
                    "u.rowid = f.rowid"
                    if fts_schema and "content='users'" in fts_schema[0]
                    else "u.user_id = f.user_id"
                )

                dir_sql = 'ASC' if order == 'asc' else 'DESC'

                if sort == 'relevance' and not q:
                    sort = 'user_id'

                params = []

                if has_fts and q:
                    sanitized_q = '"' + q.replace('"', '""') + '"'
                    base = (
                        "SELECT u.user_id, u.username, u.user_title, u.user_rank, "
                        "u.reputation, u.post_count, u.thread_count, u.user_stars, "
                        "u.user_group, u.joined, u.awards, u.past_usernames "
                        f"FROM fts_users f JOIN users u ON {fts_join} "
                        "WHERE fts_users MATCH ?"
                    )
                    params.append(sanitized_q)
                else:
                    base = (
                        "SELECT user_id, username, user_title, user_rank, "
                        "reputation, post_count, thread_count, user_stars, "
                        "user_group, joined, awards, past_usernames FROM users WHERE 1=1"
                    )

                t = 'u.' if (has_fts and q) else ''
                if sort == 'relevance' and q and has_fts:
                    pass
                elif sort == 'user_id':
                    base += f" ORDER BY CAST({t}user_id AS INTEGER) {dir_sql}"
                elif sort == 'username':
                    base += f" ORDER BY {t}username {dir_sql}"
                elif sort == 'post_count':
                    base += f" ORDER BY CAST({t}post_count AS INTEGER) {dir_sql}"
                elif sort == 'reputation':
                    base += f" ORDER BY CAST({t}reputation AS INTEGER) {dir_sql}"
                elif sort == 'joined':
                    base += f" ORDER BY {t}joined {dir_sql}"

                try:
                    cur.execute(f"SELECT COUNT(*) FROM ({base})", params)
                    total = cur.fetchone()[0]
                except Exception:
                    total = None

                base += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                try:
                    cur.execute(base, params)
                    rows = cur.fetchall()
                except Exception as e:
                    conn.close()
                    return send_json({'error': str(e)}, 500)

                out = []
                for r in rows:
                    out.append({
                        'user_id':        str(r['user_id']),
                        'username':       r['username'] or '',
                        'user_title':     r['user_title'] or '',
                        'user_rank':      r['user_rank'] or '',
                        'reputation':     r['reputation'] or '',
                        'post_count':     r['post_count'] or '',
                        'thread_count':   r['thread_count'] or '',
                        'user_stars':     r['user_stars'] or 0,
                        'user_group':     _parse_json_field(r['user_group']),
                        'joined':         r['joined'] or '',
                        'awards':         _parse_awards_field(r['awards']),
                        'past_usernames': _parse_json_field(r['past_usernames']) or [],
                    })

                conn.close()
                return send_json({'results': out, 'total': total, 'offset': offset, 'limit': limit})

            return send_json({'error': 'unknown api'}, 404)

        except Exception as e:
            return send_json({'error': str(e)}, 500)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, format, *args):
        pass

# ── Startup ───────────────────────────────────────────────────────────────────
def run_http_server():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    with socketserver.ThreadingTCPServer((HOST, PORT), Handler) as httpd:
        httpd.serve_forever()

def start_iframely():
    print("Starting Iframely...")
    subprocess.run(["docker", "compose", "up", "-d", "iframely"], check=True)
    print("Iframely running on http://localhost:8061")

def stop_iframely():
    print("\nStopping Iframely...")
    subprocess.run(["docker", "compose", "down", "iframely"])

if __name__ == "__main__":
    if MANAGE_IFRAMELY:
        start_iframely()
    print(f"Archive running on http://{HOST}:{PORT}/static/search.html")
    print("   Press Ctrl+C to stop everything\n")

    thread = threading.Thread(target=run_http_server, daemon=True)
    thread.start()

    stop_event = threading.Event()

    def shutdown(sig, frame):
        if MANAGE_IFRAMELY:
            stop_iframely()
        print("Bye")
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    stop_event.wait()
