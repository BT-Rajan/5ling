"""The monitor (BRD NFR-OB-01). Run every minute (deploy/systemd/ledgerline-monitor.timer).

It tells the technical owner, by email sent straight to the mail server (not through the
outbox, whose worker may be the thing that failed), about:

- a job run recorded as failed (the cycle generator), and the generator not having succeeded for
  26 hours;
- client emails that failed or bounced in the last hour (one alert per hour at most);
- server errors: `error_spike_threshold` or more in the last 5 minutes (one alert per 10 minutes).

Systemd services alert the moment they fail too (OnFailure, `notify-failure`). Each alert is
recorded once per key, so running every minute never repeats one; an alert the mail server
refused is tried again on the next run, up to five times, and logged CRITICAL either way.
Because the monitor runs every minute, an alert goes out within 5 minutes of the failure.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.mail.provider import EmailProvider, Outgoing
from app.models import (
    Email,
    EmailStatus,
    ErrorMinute,
    JobRun,
    JobStatus,
    OpsAlert,
    OpsAlertKind,
)
from app.timeutil import ensure_utc, utcnow

log = logging.getLogger("ledgerline.ops")

GENERATOR_JOB = "cycle_generator"
MISSED_AFTER = timedelta(hours=26)
SPIKE_WINDOW = timedelta(minutes=5)
MAX_ATTEMPTS = 5


@dataclass
class Tally:
    raised: int = 0
    delivered: int = 0
    failed: int = 0
    kinds: list[str] = field(default_factory=list)


def _raise(db: Session, kind: OpsAlertKind, key: str, subject: str, detail: str) -> bool:
    """Record an alert once. True if it is new."""
    try:
        with db.begin_nested():
            db.add(
                OpsAlert(
                    kind=kind, dedupe_key=key[:120], subject=subject[:200], detail=detail[:1000]
                )
            )
        return True
    except IntegrityError:
        return False


def _checks(db: Session, settings: Settings, now: datetime) -> None:
    for run in db.scalars(
        select(JobRun).where(
            JobRun.status == JobStatus.FAILED, JobRun.started_at >= now - timedelta(days=1)
        )
    ):
        _raise(
            db,
            OpsAlertKind.JOB_FAILED,
            f"job_run:{run.id}",
            f"Job failed: {run.job}",
            f"The {run.job} run started {ensure_utc(run.started_at).isoformat()} failed: "
            f"{run.error or 'see the logs'}. {run.clients_failed} clients failed.",
        )
    last_ok = db.scalar(
        select(func.max(JobRun.started_at)).where(
            JobRun.job == GENERATOR_JOB, JobRun.status == JobStatus.OK
        )
    )
    if last_ok is not None and now - ensure_utc(last_ok) > MISSED_AFTER:
        _raise(
            db,
            OpsAlertKind.JOB_MISSED,
            f"missed:{GENERATOR_JOB}:{now:%Y-%m-%d}",
            "The cycle generator has not run",
            f"No successful cycle generator run since {ensure_utc(last_ok).isoformat()}."
            " Check ledgerline-cycles.timer.",
        )
    failed = int(
        db.scalar(
            select(func.count(Email.id)).where(
                Email.status.in_((EmailStatus.FAILED, EmailStatus.BOUNCED)),
                Email.updated_at >= now - timedelta(hours=1),
            )
        )
        or 0
    )
    if failed:
        _raise(
            db,
            OpsAlertKind.EMAILS_FAILED,
            f"emails:{now:%Y-%m-%dT%H}",
            f"{failed} client emails failed in the last hour",
            f"{failed} emails failed or bounced in the last hour. They are listed on the email"
            " page (Setup, Email) and on the owners' work lists.",
        )
    errors = int(
        db.scalar(
            select(func.coalesce(func.sum(ErrorMinute.count), 0)).where(
                ErrorMinute.minute >= now - SPIKE_WINDOW
            )
        )
        or 0
    )
    if errors >= settings.error_spike_threshold:
        bucket = now.replace(minute=now.minute - now.minute % 10, second=0, microsecond=0)
        _raise(
            db,
            OpsAlertKind.ERROR_SPIKE,
            f"errors:{bucket:%Y-%m-%dT%H:%M}",
            f"{errors} server errors in 5 minutes",
            f"{errors} requests failed with a server error in the last 5 minutes (threshold"
            f" {settings.error_spike_threshold}). Check the API's logs.",
        )


def deliver(db: Session, settings: Settings, provider: EmailProvider, tally: Tally) -> None:
    """Send every alert not yet delivered (and not given up on) to the technical owner."""
    pending = list(
        db.scalars(
            select(OpsAlert)
            .where(OpsAlert.delivered.is_(False), OpsAlert.attempts < MAX_ATTEMPTS)
            .order_by(OpsAlert.id)
            .with_for_update(skip_locked=True)
        )
    )
    for alert in pending:
        tally.kinds.append(alert.kind.value)
        log.critical(
            "OPS ALERT", extra={"ctx": {"kind": alert.kind.value, "subject": alert.subject}}
        )
        alert.attempts += 1
        if settings.tech_owner_email is None:
            alert.last_error = "no tech_owner_email set"
            tally.failed += 1
            continue
        try:
            provider.send(
                Outgoing(
                    public_id=secrets.token_hex(16),
                    to=[settings.tech_owner_email],
                    cc=[],
                    subject=f"[{settings.firm_name} Ledgerline] {alert.subject}",
                    body=f"{alert.detail}\n\nRaised {ensure_utc(alert.created_at).isoformat()}.",
                )
            )
        except Exception as exc:
            alert.last_error = f"{type(exc).__name__}: {exc}"[:300]
            tally.failed += 1
            continue
        alert.delivered = True
        alert.last_error = None
        tally.delivered += 1


def run(
    factory: sessionmaker[Session],
    settings: Settings,
    provider: EmailProvider,
    *,
    now: datetime | None = None,
) -> Tally:
    now = ensure_utc(now or utcnow())
    tally = Tally()
    with factory() as db:
        before = int(db.scalar(select(func.count(OpsAlert.id))) or 0)
        _checks(db, settings, now)
        db.commit()
        tally.raised = int(db.scalar(select(func.count(OpsAlert.id))) or 0) - before
        deliver(db, settings, provider, tally)
        db.commit()
    return tally


def unit_failed(
    factory: sessionmaker[Session], settings: Settings, provider: EmailProvider, unit: str
) -> Tally:
    """systemd OnFailure: a service failed. Alert at once (once per unit per minute)."""
    now = utcnow()
    tally = Tally()
    safe = "".join(ch for ch in unit if ch.isalnum() or ch in "@._-")[:80]
    with factory() as db:
        if _raise(
            db,
            OpsAlertKind.UNIT_FAILED,
            f"unit:{safe}:{now:%Y-%m-%dT%H:%M}",
            f"Service failed: {safe}",
            f"The systemd service {safe} failed at {now.isoformat()}."
            f" See: journalctl -u {safe} --since '-10 min'",
        ):
            tally.raised = 1
        db.commit()
        deliver(db, settings, provider, tally)
        db.commit()
    return tally
