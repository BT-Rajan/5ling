"""Writing to the outbox: drafts, queueing, status changes and delivery reports.

Everything here runs in the caller's transaction, so an email and the change it reports are
saved together or not at all. Nothing is sent from a request: the send worker
(`app.mail.worker`) picks queued emails up.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from datetime import datetime, timedelta

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.mail.templates import Rendered, render
from app.models import (
    Email,
    EmailEvent,
    EmailKind,
    EmailStatus,
    EventSource,
    Role,
    User,
)
from app.timeutil import utcnow

MAX_RECIPIENTS = 20
AUDIT_ALERT_EVERY = timedelta(hours=24)  # at most one audit alarm email a day

# The moves the database also enforces (trigger emails_forward_only).
NEXT: dict[EmailStatus, frozenset[EmailStatus]] = {
    EmailStatus.DRAFT: frozenset({EmailStatus.QUEUED}),
    EmailStatus.QUEUED: frozenset({EmailStatus.SENT, EmailStatus.FAILED}),
    EmailStatus.SENT: frozenset({EmailStatus.DELIVERED, EmailStatus.BOUNCED, EmailStatus.FAILED}),
    EmailStatus.DELIVERED: frozenset({EmailStatus.BOUNCED}),
    EmailStatus.FAILED: frozenset(),
    EmailStatus.BOUNCED: frozenset(),
}


class BadAddress(ValueError):
    pass


class WrongStatus(ValueError):
    def __init__(self, email: Email, wanted: EmailStatus) -> None:
        super().__init__(f"email {email.id} is {email.status}, cannot become {wanted}")
        self.current = email.status


def clean_addresses(addresses: Iterable[str]) -> list[str]:
    """Validated, lower-cased, without repeats. Raises BadAddress (without echoing the address:
    the message may reach a log)."""
    found: list[str] = []
    for raw in addresses:
        try:
            address = validate_email(raw, check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            raise BadAddress("That is not a valid email address.") from None
        if address not in found:
            found.append(address)
    if len(found) > MAX_RECIPIENTS:
        raise BadAddress(f"An email can go to at most {MAX_RECIPIENTS} addresses.")
    return found


def _event(
    db: Session, email: Email, source: EventSource, detail: str | None, when: datetime
) -> None:
    db.add(
        EmailEvent(
            email_id=email.id,
            status=email.status,
            source=source,
            detail=detail[:500] if detail else None,
            occurred_at=when,
        )
    )


def move(
    db: Session,
    email: Email,
    status: EmailStatus,
    source: EventSource,
    detail: str | None = None,
    *,
    when: datetime | None = None,
) -> None:
    """Change the status (forward only) and record it. The caller holds the row lock."""
    if status not in NEXT[email.status]:
        raise WrongStatus(email, status)
    when = when or utcnow()
    email.status = status
    if status is EmailStatus.SENT:
        email.sent_at = when
    elif status is EmailStatus.DELIVERED:
        email.delivered_at = when
    elif status in (EmailStatus.FAILED, EmailStatus.BOUNCED):
        email.failed_at = when
    if status is not EmailStatus.QUEUED:
        email.next_attempt_at = None
    _event(db, email, source, detail, when)
    db.flush()  # one UPDATE per step, so the database checks each move on its own


def create(
    db: Session,
    *,
    kind: EmailKind,
    to: Iterable[str],
    content: Rendered,
    cc: Iterable[str] = (),
    client_pk: int | None = None,
    ticket_id: int | None = None,
    state_event_id: int | None = None,
    created_by_id: int | None = None,
    queue_now: bool = False,
) -> Email:
    """A draft (nothing is sent until it is queued), or with `queue_now`, queued at once."""
    recipients = clean_addresses(to)
    if not recipients:
        raise BadAddress("An email needs at least one recipient.")
    now = utcnow()
    email = Email(
        public_id=secrets.token_hex(16),
        kind=kind,
        status=EmailStatus.DRAFT,
        to_addresses=recipients,
        cc_addresses=[a for a in clean_addresses(cc) if a not in recipients],
        subject=content.subject,
        body_text=content.body,
        client_pk=client_pk,
        ticket_id=ticket_id,
        state_event_id=state_event_id,
        created_by_id=created_by_id,
        created_at=now,
        attempts=0,
    )
    db.add(email)
    db.flush()
    _event(db, email, EventSource.APP, "drafted", now)
    if queue_now:
        queue(db, email, by_id=created_by_id)
    return email


def queue(db: Session, email: Email, *, by_id: int | None) -> None:
    """Hand a draft to the send worker. Its text is final from here (enforced by a trigger)."""
    now = utcnow()
    email.queued_at = now
    email.queued_by_id = by_id
    email.next_attempt_at = now
    move(db, email, EmailStatus.QUEUED, EventSource.APP, "queued", when=now)


def active_admin_addresses(db: Session) -> list[str]:
    return list(
        db.scalars(
            select(User.email)
            .where(User.role == Role.ADMINISTRATOR, User.is_active.is_(True))
            .order_by(User.id)
        )
    )


def alert_admins_audit_failed(db: Session, detail: str, firm_name: str) -> Email | None:
    """FR-AU-03: email every Administrator that the audit check failed, at most once a day.

    The on-screen alarm and the CRITICAL log line do not depend on this.
    """
    since = utcnow() - AUDIT_ALERT_EVERY
    recent = db.scalar(
        select(Email.id)
        .where(Email.kind == EmailKind.AUDIT_ALERT, Email.created_at >= since)
        .limit(1)
    )
    admins = active_admin_addresses(db)
    if recent is not None or not admins:
        return None
    content = render("audit_alert", {"firm_name": firm_name, "detail": detail})
    return create(db, kind=EmailKind.AUDIT_ALERT, to=admins, content=content, queue_now=True)


REPORTABLE = {
    "delivered": EmailStatus.DELIVERED,
    "bounced": EmailStatus.BOUNCED,
    "failed": EmailStatus.FAILED,
}


def apply_report(
    db: Session, public_id: str, status: EmailStatus, detail: str | None
) -> Email | None:
    """A delivery report from the provider. Returns the email if it changed, None otherwise.

    Reports can arrive twice or out of order; one that does not move the status forward is
    ignored rather than refused, so the provider does not keep retrying it.
    """
    email = db.scalar(
        select(Email)
        .where(Email.public_id == public_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if email is None:
        return None
    if email.status is EmailStatus.QUEUED and status is not EmailStatus.FAILED:
        # The report beat the worker's own record of the send: the provider clearly has it.
        move(db, email, EmailStatus.SENT, EventSource.PROVIDER, "accepted by the provider")
    if status not in NEXT[email.status]:
        return None
    move(db, email, status, EventSource.PROVIDER, detail)
    return email
