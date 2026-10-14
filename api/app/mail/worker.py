"""The send worker (BRD INT-01): sends queued email and retries for 24 hours.

Each round:
1. Claims up to `batch` due emails with `FOR UPDATE SKIP LOCKED`, so several workers never take
   the same one, and pushes their next try 10 minutes out (a lease) before letting go of the
   lock. Sending happens outside any transaction, so a slow mail server holds no row locks.
2. Sends each one, then records the result on the row:
   - accepted: `sent`;
   - temporary failure: tried again after 1, 5, 15, 30 and then every 60 minutes, until 24 hours
     after it was queued, when it becomes `failed`;
   - permanent failure (refused): `failed` at once.

If the worker dies mid-send, the lease runs out and the email is tried again, with the same
Message-ID. That can send a duplicate in the rare case the provider had already accepted it;
never sending at all would be worse.

Failures are logged (without addresses) and shown in the outbox; chunk C15 shows a failed
client email to the client's owner.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.mail.outbox import move
from app.mail.provider import EmailProvider, Outgoing, PermanentFailure, TemporaryFailure
from app.models import Email, EmailEvent, EmailStatus, EventSource
from app.timeutil import ensure_utc, utcnow

log = logging.getLogger("ledgerline.mail")

RETRY_FOR = timedelta(hours=24)
LEASE = timedelta(minutes=10)
_DELAYS_MINUTES = (1, 5, 15, 30)
_LAST_DELAY = timedelta(minutes=60)
BATCH = 50


def retry_delay(attempts: int) -> timedelta:
    """The wait after the given number of failed tries."""
    if 1 <= attempts <= len(_DELAYS_MINUTES):
        return timedelta(minutes=_DELAYS_MINUTES[attempts - 1])
    return _LAST_DELAY


@dataclass
class Tally:
    sent: int = 0
    retrying: int = 0
    failed: int = 0

    @property
    def handled(self) -> int:
        return self.sent + self.retrying + self.failed


def claim(factory: sessionmaker[Session], now: datetime, batch: int) -> list[int]:
    with factory() as db:
        emails = list(
            db.scalars(
                select(Email)
                .where(Email.status == EmailStatus.QUEUED, Email.next_attempt_at <= now)
                .order_by(Email.next_attempt_at, Email.id)
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        )
        for email in emails:
            email.attempts += 1
            email.next_attempt_at = now + LEASE
        db.commit()
        return [e.id for e in emails]


def _outgoing(factory: sessionmaker[Session], email_id: int) -> Outgoing | None:
    with factory() as db:
        email = db.get(Email, email_id)
        if email is None or email.status is not EmailStatus.QUEUED:
            return None
        return Outgoing(
            public_id=email.public_id,
            to=list(email.to_addresses),
            cc=list(email.cc_addresses),
            subject=email.subject,
            body=email.body_text,
        )


def _record(
    factory: sessionmaker[Session],
    email_id: int,
    *,
    provider_id: str | None,
    error: Exception | None,
    tally: Tally,
    now: datetime,
) -> None:
    with factory() as db:
        email = db.scalar(
            select(Email)
            .where(Email.id == email_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if email is None or email.status is not EmailStatus.QUEUED:
            return  # a delivery report already moved it on
        context = {"email": email.public_id, "kind": email.kind.value, "try": email.attempts}
        if error is None:
            email.provider_message_id = (provider_id or "")[:250] or None
            email.last_error = None
            note = f"accepted on try {email.attempts}"
            move(db, email, EmailStatus.SENT, EventSource.WORKER, note, when=now)
            tally.sent += 1
        else:
            email.last_error = str(error)[:500]
            queued_at = ensure_utc(email.queued_at) if email.queued_at else now
            out_of_time = now - queued_at >= RETRY_FOR
            if isinstance(error, PermanentFailure) or out_of_time:
                why = (
                    "refused" if isinstance(error, PermanentFailure) else "not sent within 24 hours"
                )
                move(db, email, EmailStatus.FAILED, EventSource.WORKER, f"{why}: {error}", when=now)
                tally.failed += 1
                log.warning("email failed", extra={"ctx": context | {"reason": why}})
            else:
                email.next_attempt_at = now + retry_delay(email.attempts)
                db.add(
                    EmailEvent(
                        email_id=email.id,
                        status=EmailStatus.QUEUED,
                        source=EventSource.WORKER,
                        detail=f"try {email.attempts} failed: {error}"[:500],
                        occurred_at=now,
                    )
                )
                tally.retrying += 1
                log.info("email will be retried", extra={"ctx": context})
        db.commit()


def send_due(
    factory: sessionmaker[Session],
    provider: EmailProvider,
    *,
    now: datetime | None = None,
    batch: int = BATCH,
) -> Tally:
    """One round: send what is due now. Returns what happened."""
    tally = Tally()
    now = now or utcnow()
    for email_id in claim(factory, now, batch):
        message = _outgoing(factory, email_id)
        if message is None:
            continue
        try:
            provider_id = provider.send(message)
        except (TemporaryFailure, PermanentFailure) as exc:
            _record(factory, email_id, provider_id=None, error=exc, tally=tally, now=now)
        except Exception as exc:  # a bug in a provider: retry, and say so loudly
            log.exception("email provider raised unexpectedly")
            _record(
                factory,
                email_id,
                provider_id=None,
                error=TemporaryFailure(f"unexpected error ({type(exc).__name__})"),
                tally=tally,
                now=now,
            )
        else:
            _record(factory, email_id, provider_id=provider_id, error=None, tally=tally, now=now)
    return tally


def run_forever(
    factory: sessionmaker[Session],
    provider: EmailProvider,
    *,
    interval_seconds: float = 15,
    stop: threading.Event | None = None,
    before_round: Callable[[], object] | None = None,
) -> None:
    """The long-running worker (deploy/systemd/ledgerline-mailer.service)."""
    stop = stop or threading.Event()
    log.info("email worker started")
    while not stop.is_set():
        try:
            if before_round is not None:
                before_round()
            tally = send_due(factory, provider)
        except Exception:
            log.exception("email worker round failed; trying again shortly")
            tally = Tally()
        if tally.handled:
            log.info(
                "email worker round",
                extra={
                    "ctx": {"sent": tally.sent, "retrying": tally.retrying, "failed": tally.failed}
                },
            )
        # A full batch means more are waiting: go again straight away.
        if tally.handled < BATCH:
            stop.wait(interval_seconds)
    log.info("email worker stopped")
