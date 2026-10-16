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

from app.alerts.job import run as run_alerts
from app.audit import audit
from app.audit.verify import PROBLEMS, run_check
from app.auth import passwords
from app.auth.sessions import revoke_all_for_user
from app.config import get_settings
from app.cycles.generator import run as run_generator
from app.db import make_engine, make_session_factory
from app.fees.job import run as run_invoices
from app.files.documents import scan_pending
from app.files.scan import ClamdScanner
from app.files.store import store_from_settings
from app.logging_conf import setup_logging
from app.mail.provider import provider_from_settings
from app.mail.updates import sweep
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
from app.notify.service import documents_arrived
from app.ops.monitor import run as run_monitor
from app.ops.monitor import unit_failed
from app.ops.restore import check as restore_check
from app.security_check import review

# Go-live tools (C32) are imported where used: they build the whole app in process.


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
        drafted = sweep(factory, settings.firm_name)
        if once:
            tally = send_due(factory, provider)
            return (
                True,
                f"{drafted} drafted, {tally.sent} sent, {tally.retrying} to retry, "
                f"{tally.failed} failed.",
            )
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        run_forever(
            factory,
            provider,
            interval_seconds=interval,
            stop=stop,
            before_round=lambda: sweep(factory, settings.firm_name),
        )
        return True, "Email worker stopped."
    finally:
        engine.dispose()


def scan_documents() -> tuple[bool, str]:
    """Scan files held because the virus scanner was down (INT-03). Run by a systemd timer."""
    settings = get_settings()
    setup_logging(settings.log_level)
    engine = make_engine(settings.database_url.get_secret_value())
    try:
        tally = scan_pending(
            make_session_factory(engine),
            store_from_settings(settings),
            ClamdScanner(settings.clamd_address),
            on_clean=lambda db, cycle_id: documents_arrived(
                db, cycle_id, firm_name=settings.firm_name
            ),
        )
    finally:
        engine.dispose()
    summary = f"{tally.clean} clean, {tally.infected} infected, {tally.waiting} still waiting."
    if tally.errors:
        return False, f"Some held files could not be read: {'; '.join(tally.errors)} {summary}"
    if tally.waiting:
        return False, f"The virus scanner is not reachable. {summary}"
    return True, summary


def send_alerts() -> tuple[bool, str]:
    """Automatic client alerts and reminders (FR-AL-01, FR-AL-03). Run every 15 minutes."""
    settings = get_settings()
    setup_logging(settings.log_level)
    engine = make_engine(settings.database_url.get_secret_value())
    try:
        tally = run_alerts(make_session_factory(engine), settings)
    finally:
        engine.dispose()
    if tally.skipped_reason:
        return True, f"No alerts sent: {tally.skipped_reason}."
    summary = f"{tally.sent} alerts sent, {tally.checked} cycles checked, {tally.failed} failed."
    if tally.failed:
        return False, f"CLIENT ALERTS FAILED: {summary} {'; '.join(tally.failures)}"
    return True, summary


def raise_invoices() -> tuple[bool, str]:
    """Invoices on each client's billing schedule (FR-FE-02). Run daily."""
    settings = get_settings()
    setup_logging(settings.log_level)
    engine = make_engine(settings.database_url.get_secret_value())
    try:
        tally = run_invoices(make_session_factory(engine), settings)
    finally:
        engine.dispose()
    if tally.skipped_reason:
        return False, f"Invoices skipped: {tally.skipped_reason}."
    summary = f"{tally.raised} invoices raised for {tally.checked} clients, {tally.failed} failed."
    if tally.failed:
        return False, f"INVOICE RUN FAILED: {summary} {'; '.join(tally.failures)}"
    return True, summary


def security_check() -> tuple[bool, str]:
    """NFR-SE-04: encryption at rest, in transit, and key separation, on this installation."""
    settings = get_settings()
    engine = make_engine(settings.database_url.get_secret_value())
    try:
        checks = review(settings, engine)
    finally:
        engine.dispose()
    lines = [f"{'PASS' if c.ok else 'FAIL'}  {c.name}: {c.detail}" for c in checks]
    return all(c.ok for c in checks), "\n".join(lines)


def monitor(unit: str | None = None) -> tuple[bool, str]:
    """NFR-OB-01: alert the technical owner (every minute; or at once for a failed service)."""
    settings = get_settings()
    setup_logging(settings.log_level)
    engine = make_engine(settings.database_url.get_secret_value())
    try:
        factory = make_session_factory(engine)
        provider = provider_from_settings(settings)
        tally = (
            unit_failed(factory, settings, provider, unit)
            if unit
            else run_monitor(factory, settings, provider)
        )
    finally:
        engine.dispose()
    summary = f"{tally.raised} new alerts, {tally.delivered} sent, {tally.failed} not sent."
    return tally.failed == 0, summary


def verify_restore(backup: str) -> tuple[bool, str]:
    """NFR-AV-02: check a restored backup (run by scripts/restore-test on the scratch database)."""
    settings = get_settings()
    engine = make_engine(settings.database_url.get_secret_value())
    try:
        with make_session_factory(engine)() as db:
            lines = restore_check(db, store_from_settings(settings), backup)
    finally:
        engine.dispose()
    return all(line.ok for line in lines), "\n".join(
        f"{'PASS' if line.ok else 'FAIL'}  {line.text}" for line in lines
    )


def migrate_clients(
    path: str, admin_email: str, *, commit: bool, only_verify: bool, out: str | None
) -> tuple[bool, str]:
    """C32, LL-132: check, save and verify the source system's clients (see app/golive/migrate)."""
    from pathlib import Path

    from app.golive import migrate
    from app.golive.inproc import Api

    source = Path(path)
    settings = get_settings()
    setup_logging(settings.log_level)
    with Api(settings, admin_email) as api, api.factory() as db:
        report = migrate.check(db, migrate.read(source))
        if commit:
            migrate.commit(db, api, report)
            target = Path(out) if out else source.with_suffix(".map.csv")
            migrate.write_map(report, target)
        if commit or only_verify:
            migrate.verify(db, report)
    text = report.text()
    if not commit and not only_verify and not report.bad:
        text += "\nAll rows are ready. Run again with --commit to save them."
    return report.ok, text


def load_catalog(path: str, admin_email: str, *, commit: bool) -> tuple[bool, str]:
    """C32, LL-133: load filing types, due-date rules and packages (see app/golive/catalog)."""
    from pathlib import Path

    from app.golive import catalog
    from app.golive.inproc import Api

    settings = get_settings()
    setup_logging(settings.log_level)
    parsed, result = catalog.read(Path(path))
    if parsed is None:
        return False, result.text()
    with Api(settings, admin_email) as api:
        catalog.check(api, parsed, result)
        if commit:
            catalog.commit(api, parsed, result)
        elif result.ok:
            result.done.append("All entries are ready. Run again with --commit to load them.")
    return result.ok, result.text()


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
    sub.add_parser("scan-documents", help="scan files held while the virus scanner was down")
    sub.add_parser("send-alerts", help="send due client alerts and reminders (every 15 min)")
    sub.add_parser("raise-invoices", help="raise invoices due on the billing schedule (daily)")
    sub.add_parser("monitor", help="alert the technical owner about failures (every minute)")
    failed = sub.add_parser("notify-failure", help="alert at once that a systemd service failed")
    failed.add_argument("unit")
    restore = sub.add_parser(
        "verify-restore", help="check a restored backup (scripts/restore-test)"
    )
    restore.add_argument("--backup", required=True, help="the backup file's name")
    sub.add_parser(
        "security-check", help="check encryption at rest and key separation (exit 1 if not)"
    )
    mig = sub.add_parser("migrate-clients", help="check, then save, the source system's clients")
    mig.add_argument("file", help="the clients CSV (see docs/GOLIVE.md)")
    mig.add_argument("--as", dest="admin", required=True, help="an Administrator's email")
    mode = mig.add_mutually_exclusive_group()
    mode.add_argument("--commit", action="store_true", help="save the rows (after a clean check)")
    mode.add_argument("--verify", action="store_true", help="only the count and sample checks")
    mig.add_argument("--out", help="where to write the source-to-client id map")
    cat = sub.add_parser("load-catalog", help="load filing types, due dates and packages (JSON)")
    cat.add_argument("file")
    cat.add_argument("--as", dest="admin", required=True, help="an Administrator's email")
    cat.add_argument("--commit", action="store_true", help="load them (after a clean check)")
    args = parser.parse_args(argv)

    if args.command == "migrate-clients":
        ok, message = migrate_clients(
            args.file, args.admin, commit=args.commit, only_verify=args.verify, out=args.out
        )
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.command == "load-catalog":
        ok, message = load_catalog(args.file, args.admin, commit=args.commit)
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.command in ("monitor", "notify-failure"):
        ok, message = monitor(args.unit if args.command == "notify-failure" else None)
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.command == "verify-restore":
        ok, message = verify_restore(args.backup)
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.command == "security-check":
        ok, message = security_check()
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.command == "raise-invoices":
        ok, message = raise_invoices()
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.command == "send-alerts":
        ok, message = send_alerts()
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.command == "scan-documents":
        ok, message = scan_documents()
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

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
