"""Fee reminders (FR-FE-07, chunk C35): to the client, at set intervals after the due date.

An invoice is reminded about while it has money owing and its due date has passed: the first
reminder `interval` days after the due date, then every `interval` days (the Administrator's
rule), at most `MAX_REMINDERS` times. They stop the moment it is paid, because each run checks the
balance from the ledger. Closed clients are not reminded.

Each reminder is one email (sent at once, not a draft: CR-03 covers state changes, and this is a
scheduled message like the document reminders) and one `fee_reminders` row. The row is unique per
invoice and India date, so two runs cannot remind twice in a day.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.audit import audit
from app.catalog import rupees
from app.fees.ledger import paid_by_invoice
from app.mail.outbox import create
from app.mail.templates import render
from app.models import (
    Client,
    ClientStatus,
    Contact,
    EmailKind,
    FeeReminder,
    Invoice,
)
from app.rules import rules

log = logging.getLogger("ledgerline.jobs")

MAX_REMINDERS = 6


@dataclass
class ReminderTally:
    sent: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)


def next_due(due_date: date, last_sent: date | None, interval: int) -> date:
    """When the next reminder is due: `interval` days after the due date, or after the last one."""
    return (last_sent or due_date) + timedelta(days=interval)


def due_invoices(db: Session, today: date, interval: int) -> list[tuple[int, int]]:
    """(invoice id, balance in paise) for every invoice owed a reminder today."""
    candidates = list(
        db.execute(
            select(Invoice.id, Invoice.total_paise, Invoice.due_date)
            .join(Client, Client.id == Invoice.client_pk)
            .where(
                Client.status != ClientStatus.CLOSED,
                Invoice.due_date <= today - timedelta(days=interval),
            )
            .order_by(Invoice.due_date, Invoice.id)
        )
    )
    if not candidates:
        return []
    ids = [i for i, _, _ in candidates]
    paid = paid_by_invoice(db, ids)
    history = {
        invoice_id: (count, last)
        for invoice_id, count, last in db.execute(
            select(FeeReminder.invoice_id, func.count(), func.max(FeeReminder.sent_on))
            .where(FeeReminder.invoice_id.in_(ids))
            .group_by(FeeReminder.invoice_id)
        )
    }
    out = []
    for invoice_id, total, due in candidates:
        balance = total - paid.get(invoice_id, 0)
        count, last = history.get(invoice_id, (0, None))
        if balance > 0 and count < MAX_REMINDERS and next_due(due, last, interval) <= today:
            out.append((invoice_id, balance))
    return out


def remind_one(db: Session, invoice_id: int, balance: int, firm_name: str, today: date) -> bool:
    """Send one reminder for one invoice. Caller commits (or rolls back on a duplicate)."""
    invoice = db.get(Invoice, invoice_id)
    client = db.get(Client, invoice.client_pk) if invoice else None
    contact = (
        db.scalar(
            select(Contact).where(Contact.client_pk == client.id, Contact.is_primary.is_(True))
        )
        if client
        else None
    )
    if invoice is None or client is None or contact is None:
        return False
    owed = rupees(balance)
    content = render(
        "fee_reminder",
        {
            "firm_name": firm_name,
            "client_name": client.legal_name,
            "client_id": client.client_id,
            "detail": f"{invoice.invoice_no} ({invoice.period_label}), Rs. {owed} owing",
            "due_date": invoice.due_date,
        },
    )
    email = create(
        db,
        kind=EmailKind.FEE_REMINDER,
        to=[contact.email],
        content=content,
        client_pk=client.id,
        queue_now=True,
    )
    db.add(
        FeeReminder(invoice_id=invoice.id, email_id=email.id, sent_on=today, balance_paise=balance)
    )
    db.flush()  # the unique (invoice, day) refuses a second reminder today
    audit(
        db,
        "fee.reminder_sent",
        target=("invoice", invoice.invoice_no),
        details={"balance_paise": balance, "email": email.id},
    )
    return True


def send_fee_reminders(
    factory: sessionmaker[Session], *, today: date, firm_name: str
) -> ReminderTally:
    """Run inside the alerts job's lock and sending hours."""
    tally = ReminderTally()
    with factory() as db:
        interval = rules(db).fee_reminder_interval_days
        due = due_invoices(db, today, interval)
    for invoice_id, balance in due:
        with factory() as db:
            try:
                if remind_one(db, invoice_id, balance, firm_name, today):
                    db.commit()
                    tally.sent += 1
                else:
                    db.rollback()
            except IntegrityError:
                db.rollback()  # another run reminded about it today, a moment ago
            except Exception as exc:  # one bad invoice must not stop the rest
                db.rollback()
                tally.failed += 1
                tally.failures.append(f"invoice {invoice_id}: {type(exc).__name__}")
                log.exception("fee reminder failed", extra={"ctx": {"invoice": invoice_id}})
    return tally
