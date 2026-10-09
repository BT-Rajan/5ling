"""Command-line tools. The first Administrator cannot be created from the web, so it is made here.

python -m app.cli create-admin --email you@firm.in --name "Your Name"
(the password is typed at a hidden prompt, or piped in with --password-stdin)
"""

from __future__ import annotations

import argparse
import getpass
import sys

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select

from app.auth import passwords
from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.models import Role, User


def create_admin(email: str, name: str, password: str) -> str:
    try:
        email = validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        return f"Not a valid email: {exc}"
    problems = passwords.check_policy(password, email=email, full_name=name)
    if problems:
        return "Password not accepted: " + " ".join(problems)
    engine = make_engine(get_settings().database_url.get_secret_value())
    with make_session_factory(engine)() as db:
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            return "Someone with that email already exists."
        db.add(
            User(
                email=email,
                full_name=" ".join(name.split()),
                role=Role.ADMINISTRATOR,
                password_hash=passwords.hash_password(password),
            )
        )
        db.commit()
    engine.dispose()
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("create-admin", help="create an Administrator account")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)
    admin.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    args = parser.parse_args(argv)

    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            print("Passwords do not match.", file=sys.stderr)
            return 1
    error = create_admin(args.email, args.name, password)
    if error:
        print(error, file=sys.stderr)
        return 1
    print(f"Administrator {args.email} created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
