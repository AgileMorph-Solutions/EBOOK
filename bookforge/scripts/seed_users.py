"""Seed a test admin + regular user and backfill legacy books to the admin.

Idempotent — safe to run multiple times.

Usage (run from the `bookforge/` directory):
    python -m scripts.seed_users
    python -m scripts.seed_users --admin-email me@example.com --admin-password s3cret

Runs `alembic upgrade head` first so the DB schema exists.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from bookforge.api.auth import hash_password
from bookforge.db import SessionLocal
from bookforge.models import BookRecord, User

DEFAULT_ADMIN_EMAIL = "admin@bookforge.local"
DEFAULT_USER_EMAIL = "user@bookforge.local"
DEFAULT_ADMIN_PASSWORD = "admin-dev-password"
DEFAULT_USER_PASSWORD = "user-dev-password"

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_ROOT = Path(os.environ.get("BOOKFORGE_LOCAL_STORE", REPO_ROOT / "artifacts")).resolve()


def _run_migrations() -> None:
    print("→ running: alembic upgrade head")
    subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=str(REPO_ROOT),
        check=True,
    )


def _upsert_user(db, email: str, password: str, is_admin: bool) -> User:
    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None:
        user = User(email=email, hashed_password=hash_password(password), is_admin=is_admin)
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"  + created {'admin' if is_admin else 'user'}: {email}")
    else:
        print(f"  = already exists: {email}")
    return user


def _backfill_legacy(db, admin: User) -> int:
    books_dir = ARTIFACTS_ROOT / "books"
    if not books_dir.is_dir():
        return 0
    added = 0
    for status_path in books_dir.glob("*/status.json"):
        book_id = status_path.parent.name
        if db.get(BookRecord, book_id) is not None:
            continue
        try:
            state = json.loads(status_path.read_text())
        except Exception:
            state = {}
        db.add(BookRecord(book_id=book_id, user_id=admin.id, topic=state.get("topic", "")))
        added += 1
    if added:
        db.commit()
    return added


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--admin-email", default=DEFAULT_ADMIN_EMAIL)
    p.add_argument("--user-email", default=DEFAULT_USER_EMAIL)
    p.add_argument(
        "--admin-password",
        default=os.environ.get("SEED_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD),
    )
    p.add_argument(
        "--user-password",
        default=os.environ.get("SEED_USER_PASSWORD", DEFAULT_USER_PASSWORD),
    )
    p.add_argument("--skip-migrations", action="store_true")
    args = p.parse_args()

    if not args.skip_migrations:
        _run_migrations()

    if args.admin_password == DEFAULT_ADMIN_PASSWORD or args.user_password == DEFAULT_USER_PASSWORD:
        print("⚠  using default dev passwords — rotate before any non-local deployment.")

    with SessionLocal() as db:
        admin = _upsert_user(db, args.admin_email, args.admin_password, is_admin=True)
        _upsert_user(db, args.user_email, args.user_password, is_admin=False)
        backfilled = _backfill_legacy(db, admin)
        print(f"→ backfilled {backfilled} legacy book(s) to admin {admin.email}")

    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
