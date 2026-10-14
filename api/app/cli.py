"""Command-line tools. The first Administrator cannot be created from the web, so it is made here.

python -m app.cli create-admin --email you@firm.in --name "Your Name"
(the password is typed at a hidden prompt, or piped in with --password-stdin)
"""

from __future__ import annotations

import argparse
import getpass
import signal
import sys
import threading

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import delete, select

from app.audit import audit
from app.audit.verify import PROBLEMS, run_check
from app.auth import passwords
from app.auth.sessions import revoke_all_for_user
from app.config import get_settings
from app.cycles.generator import run as run_generator
from app.db import make_engine, make_session_factory
from app.logging_conf import setup_logging
from app.mail.provider import provider_from_settings
from app.mail.worker import run_forever, send_due
from app.models import (
    CheckTrigger,
    JobStatus,
    JobTrigger,
    LoginChallenge,
    RecoveryCode,
    Role,
    User,
)


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
        user = User(
            email=email,
            full_name=" ".join(name.split()),
            role=Role.ADMINISTRATOR,
            password_hash=passwords.hash_password(password),
        )
        db.add(user)
        db.flush()
        audit(
            db, "user.created", target=("user", user.id), details={"role": user.role, "via": "cli"}
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
        audit(db, "user.mfa_reset", target=("user", user.id), details={"via": "cli"})
        db.commit()
    engine.dispose()
    return ""


def verify_audit() -> tuple[bool, str]:
    """The daily check (BRD FR-AU-03). Run by deploy/systemd/ledgerline-audit-check.timer."""
    settings = get_settings()
    setup_logging(settings.log_level)
    engine = make_engine(settings.database_url.get_secret_value())
    try:
        check = run_check(
            make_session_factory(engine), CheckTrigger.SCHEDULED, firm_name=settings.firm_name
        )
    finally:
        engine.dispose()
    if check.ok:
        return True, f"Audit log intact: {check.entries_checked} entries checked."
    where = f" at entry {check.first_bad_seq}" if check.first_bad_seq else ""
    return (
        False,
        f"AUDIT LOG CHECK FAILED{where}: {PROBLEMS.get(check.problem or '', check.problem)}",
    )


def generate_cycles() -> tuple[bool, str]:
    """The daily cycle generator (FR-CY-01, FR-CY-05). Run by ledgerline-cycles.timer."""
    settings = get_settings()
    setup_logging(settings.log_level)
    engine = make_engine(settings.database_url.get_secret_value())
    try:
        result = run_generator(
            make_session_factory(engine), JobTrigger.SCHEDULED, lock_wait_seconds=600
        )
    finally:
        engine.dispose()
    summary = (
        f"{result.cycles_created} cycles created for {result.clients_checked} clients"
        f" ({result.clients_failed} failed)."
    )
    if result.status is JobStatus.FAILED:
        return False, f"CYCLE GENERATOR FAILED: {summary} {result.error or ''}".strip()
    if result.status is JobStatus.SKIPPED:
        return False, "Cycle generator skipped: another run did not finish within 10 minutes."
    return True, f"Cycle generator ran: {summary}"


def send_emails(*, once: bool, interval: float) -> tuple[bool, str]:
    """The send worker (INT-01). Run by deploy/systemd/ledgerline-mailer.service."""
    settings = get_settings()
    setup_logging(settings.log_level)
    engine = make_engine(settings.database_url.get_secret_value())
    factory = make_session_factory(engine)
    provider = provider_from_settings(settings)
    stop = threading.Event()
    try:
        if once:
            tally = send_due(factory, provider)
            return (
                True,
                f"{tally.sent} sent, {tally.retrying} to retry, {tally.failed} failed.",
            )
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        run_forever(factory, provider, interval_seconds=interval, stop=stop)
        return True, "Email worker stopped."
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("create-admin", help="create an Administrator account")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)
    admin.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    reset = sub.add_parser("reset-mfa", help="clear someone's two-step setup (emergency)")
    reset.add_argument("--email", required=True)
    sub.add_parser(
        "verify-audit", help="check the audit log's chain (the daily job; exit 1 if bad)"
    )
    sub.add_parser(
        "generate-cycles", help="make missing filing cycles (the daily job; exit 1 if any fail)"
    )
    mailer = sub.add_parser("send-emails", help="send queued email (runs until stopped)")
    mailer.add_argument("--once", action="store_true", help="one round, then exit")
    mailer.add_argument("--interval", type=float, default=15.0, help="seconds between rounds")
    args = parser.parse_args(argv)

    if args.command == "send-emails":
        if not 1 <= args.interval <= 3600:
            print("--interval must be 1 to 3600 seconds.", file=sys.stderr)
            return 2
        ok, message = send_emails(once=args.once, interval=args.interval)
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.command == "generate-cycles":
        ok, message = generate_cycles()
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.command == "verify-audit":
        ok, message = verify_audit()
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

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
