# 1397405

import sqlite3
import json
import os
import sys

user_id = sys.argv[1] if len(sys.argv) > 1 else input("Enter user_id: ").strip()

db_path = os.path.abspath("data/users.db")
print(f"Reading from: {db_path}")
print(f"Last modified: {os.path.getmtime(db_path)}")

conn = sqlite3.connect(db_path)
conn.execute("PRAGMA wal_checkpoint(FULL)")
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("SELECT COUNT(*) FROM users")
print(f"Total users in DB: {cursor.fetchone()[0]}")

cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
row = cursor.fetchone()

if row:
    user = dict(row)
    for field in ("awards", "alts", "past_usernames", "user_group"):
        if user.get(field):
            try:
                user[field] = json.loads(user[field])
            except Exception:
                pass
    print(json.dumps(user, indent=2, ensure_ascii=False))
else:
    print(f"No user found with user_id = {user_id}")

conn.close()