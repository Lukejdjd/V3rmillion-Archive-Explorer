import sqlite3
import json
import os

db_path = os.path.abspath("data/threads.db")
print(f"Reading from: {db_path}")
print(f"Last modified: {os.path.getmtime(db_path)}")

conn = sqlite3.connect(db_path)
conn.execute("PRAGMA wal_checkpoint(FULL)")
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("SELECT COUNT(*) FROM posts WHERE thread_id = '1218438'")
print(f"Post count for thread: {cursor.fetchone()[0]}")

cursor.execute("""
SELECT * FROM posts
WHERE thread_id = '1218438' AND is_op = 1
ORDER BY CAST(post_number AS INTEGER) ASC
LIMIT 1
""")

row = cursor.fetchone()
if row:
    post = dict(row)
    post["awards"] = json.loads(post["awards"])
    if post.get("user_group"):
        post["user_group"] = json.loads(post["user_group"])
    print(json.dumps(post, indent=2, ensure_ascii=False))
else:
    print("No post found.")

conn.close()