"""Command-line tools. The first Administrator cannot be created from the web, so it is made here.

python -m app.cli create-admin --email you@firm.in --name "Your Name"
(the password is typed at a hidden prompt, or piped in with --password-stdin)
"""

from __future__ import annotations

import argparse
import getpass
import sys

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import delete, select

from app.auth import passwords
from app.auth.sessions import revoke_all_for_user
from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.models import LoginChallenge, RecoveryCode, Role, User


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


def reset_mfa(email: str) -> str:
    engine = make_engine(get_settings().database_url.get_secret_value())
    with make_session_factory(engine)() as db:
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            return "No one has that email."
        if user.role is Role.CONSULTANT:
            return "Consultants do not use two-step codes."
        user.totp_secret_enc = None
        user.totp_enabled_at = None
        user.totp_last_step = None
        db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
        db.execute(delete(LoginChallenge).where(LoginChallenge.user_id == user.id))
        revoke_all_for_user(db, user.id)
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
    reset = sub.add_parser("reset-mfa", help="clear someone's two-step setup (emergency)")
    reset.add_argument("--email", required=True)
    args = parser.parse_args(argv)

    if args.command == "reset-mfa":
        error = reset_mfa(args.email)
        if error:
            print(error, file=sys.stderr)
            return 1
        print(
            f"Two-step sign-in cleared for {args.email}. They will set it up again at next sign-in."
        )
        return 0

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
