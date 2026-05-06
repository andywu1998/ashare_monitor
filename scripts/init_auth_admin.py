#!/usr/bin/env python3
"""Initialize or reset admin account for cycle_web auth."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.cycle_web.app import (  # noqa: E402
    _default_mysql_cfg_for_auth,
    _ensure_auth_tables,
    _hash_password,
    _mysql_connect,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create or reset admin user in auth_user table.")
    p.add_argument("--username", required=True, help="Admin username")
    p.add_argument("--password", required=True, help="Admin password")
    p.add_argument("--reset", action="store_true", help="Reset password if user exists")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    username = (args.username or "").strip()
    password = args.password or ""
    if not username:
        raise ValueError("username is required")
    if len(password) < 8:
        raise ValueError("password length must be >= 8")

    mysql_cfg = _default_mysql_cfg_for_auth()
    _ensure_auth_tables(mysql_cfg)

    with _mysql_connect(mysql_cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM auth_user WHERE username=%s LIMIT 1", (username,))
            row = cursor.fetchone()
            if row and not args.reset:
                print(f"[skip] user exists: {username}")
                return
            if row and args.reset:
                cursor.execute(
                    """
                    UPDATE auth_user
                    SET password_hash=%s, role='admin', is_active=1, updated_at=NOW(3)
                    WHERE id=%s
                    """,
                    (_hash_password(password), int(row["id"])),
                )
                conn.commit()
                print(f"[ok] reset admin password: {username}")
                return
            cursor.execute(
                """
                INSERT INTO auth_user (username, password_hash, role, is_active)
                VALUES (%s, %s, 'admin', 1)
                """,
                (username, _hash_password(password)),
            )
            conn.commit()
    print(f"[ok] created admin user: {username}")


if __name__ == "__main__":
    main()
