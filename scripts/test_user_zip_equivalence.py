"""Verify extracted folders and users.zip produce identical user rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import os

import profile_parser


def row_hash(row) -> str:
    return hashlib.sha256(
        json.dumps(row, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", type=int, default=1000)
    args = parser.parse_args()

    archive, entries = profile_parser.load_user_archive_index()
    checked = 0
    for user_id, user_entries in entries.items():
        profile_path = os.path.join(
            profile_parser.USERS_DIR, user_id, "profile.html"
        )
        if not os.path.isfile(profile_path):
            continue
        folder_status, folder_payload = profile_parser.parse_user_worker(user_id)
        archive_payload = profile_parser.build_user_row_from_archive(
            archive, user_id, user_entries
        )
        if folder_status != "success":
            raise RuntimeError(folder_payload)
        if row_hash(folder_payload[0]) != row_hash(archive_payload[0]):
            raise RuntimeError(f"Row differs for user {user_id}")
        checked += 1
        if checked >= args.users:
            break
    archive.close()

    if checked != args.users:
        raise RuntimeError(f"Only found {checked} comparable users")
    print(f"users={checked} row_output=identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
