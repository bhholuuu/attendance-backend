"""
Development-only script to create the initial ADMIN user.

Usage:
    python create_admin.py

This script is intended for development only. It creates a single admin
user and prevents duplicate usernames. There is no public registration API.
The password is hashed and never printed or stored in plain text.
"""

import re
import sys

from app.core.security import hash_password
from app.database.connection import SessionLocal, init_db
from app.models.user import User, UserRole

MIN_PASSWORD_LENGTH = 8


def prompt(text: str, validate=None, retries: int = 3) -> str:
    for attempt in range(retries):
        value = input(text).strip()
        if validate is None or validate(value):
            return value
        remaining = retries - attempt - 1
        print(f"Invalid input. {remaining} attempt(s) remaining.")
    print("Too many invalid attempts. Aborting.")
    sys.exit(1)


def validate_username(username: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9_.-]{3,50}$", username))


def validate_password(password: str) -> bool:
    return len(password) >= MIN_PASSWORD_LENGTH


def validate_phone(phone: str) -> bool:
    # Phone is optional; allow empty or a plausible phone string
    return phone == "" or bool(re.match(r"^\+?[0-9\s-]{7,20}$", phone))


def main() -> None:
    print("=== Create Initial Admin User (Development Only) ===")
    print("Note: This script is for development. No password will be displayed.\n")

    # Ensure tables exist
    init_db()

    full_name = prompt("Full Name: ", validate=lambda v: len(v) >= 2)
    username = prompt(
        "Username: ",
        validate=validate_username,
    )
    phone_number = prompt(
        "Phone Number (optional, press Enter to skip): ",
        validate=validate_phone,
    )
    password = prompt(
        f"Password (min {MIN_PASSWORD_LENGTH} chars): ",
        validate=validate_password,
    )

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            print(f"ERROR: A user with username '{username}' already exists.")
            sys.exit(1)

        password_hash = hash_password(password)
        admin = User(
            full_name=full_name,
            username=username,
            password_hash=password_hash,
            phone_number=phone_number or None,
            role=UserRole.ADMIN,
            is_active=True,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        print("\nAdmin user created successfully.")
        print(f"  ID:        {admin.id}")
        print(f"  Full Name: {admin.full_name}")
        print(f"  Username:  {admin.username}")
        print(f"  Role:      {admin.role.value}")
    except Exception as exc:  # pragma: no cover - defensive
        db.rollback()
        print(f"ERROR: Could not create admin user: {exc}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
