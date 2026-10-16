"""Choosing the second channel, queueing and sending (BRD FR-AL-08, INT-02, C-2; chunk C36).

- Email always goes; it is the minimum channel (FR-AL-08). WhatsApp or SMS is sent as well, to
  the primary contact, only when that contact has agreed to the channel (their latest consent
  record), has a mobile number, and the template has been approved at the provider. WhatsApp is
  preferred when both are agreed.
- Consent is checked twice: when the message is queued (with the email, in one transaction) and
  again just before it is sent, so a client who opts out in between is never messaged.
- If the message cannot be sent, the client still has the email (the fallback, INT-02), and the
  client's owner is told so they can follow up another way. Temporary failures are tried again
  on the next runs, three tries in all.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.audit import audit
from app.config import Settings
from app.messaging.provider import (
    MessagingProvider,
    OutgoingMessage,
    PermanentFailure,
    TemporaryFailure,
    provider_from_settings,
)
from app.messaging.templates import TEMPLATES, render_message
from app.models import (
    Channel,
    ChannelMessage,
    Client,
    ConsentChange,
    Contact,
    MessageStatus,
    NotificationKind,
)
from app.notify.service import notify
from app.timeutil import utcnow

log = logging.getLogger("ledgerline.jobs")

MAX_ATTEMPTS = 3
SECOND_CHANNELS = (Channel.WHATSAPP, Channel.SMS)  # in order of preference
CHANNEL_LABEL = {Channel.WHATSAPP: "WhatsApp", Channel.SMS: "SMS", Channel.EMAIL: "Email"}


def consent_of(db: Session, contact_id: int) -> dict[Channel, bool]:
    """The contact's current choice per channel: their latest consent record wins."""
    found = {Channel.EMAIL: True, Channel.WHATSAPP: False, Channel.SMS: False}
    for channel, granted in db.execute(
        select(ConsentChange.channel, ConsentChange.granted)
        .where(ConsentChange.contact_id == contact_id)
        .order_by(ConsentChange.id)
    ):
        found[channel] = granted
    found[Channel.EMAIL] = True  # email cannot be turned off (FR-AL-08)
    return found


def choose_channel(
    consent: Mapping[Channel, bool], phone: str | None, template: str, approved: set[str]
) -> Channel | None:
    """The second channel to use, or None: no agreed channel, no number, or no approval."""
    if not phone or template not in TEMPLATES or template not in approved:
        return None
    return next((c for c in SECOND_CHANNELS if consent.get(c)), None)


def queue_for_alert(
    db: Session,
    settings: Settings,
    *,
    client: Client,
    contact: Contact,
    cycle_id: int,
    email_id: int,
    template: str,
    values: Mapping[str, str | int | date],
) -> ChannelMessage | None:
    """Queue the second-channel copy of an alert, if the client agreed to one. Caller commits."""
    if settings.messaging_provider == "none":
        return None
    channel = choose_channel(
        consent_of(db, contact.id),
        contact.phone,
        template,
        set(settings.messaging_approved_templates),
    )
    if channel is None:
        return None
    text, params = render_message(template, values)
    message = ChannelMessage(
        client_pk=client.id,
        cycle_id=cycle_id,
        contact_id=contact.id,
        email_id=email_id,
        channel=channel,
        template=template,
        body_text=text,
        params=list(params),
        status=MessageStatus.QUEUED,
        attempts=0,
        created_at=utcnow(),
    )
    db.add(message)
    db.flush()
    return message


@dataclass
class DispatchTally:
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    retrying: int = 0
    failures: list[str] = field(default_factory=list)


def _finish(message: ChannelMessage, status: MessageStatus, error: str | None) -> None:
    message.status = status
    message.last_error = error[:300] if error else None
    message.finished_at = utcnow()


def _tell_owner(db: Session, message: ChannelMessage, error: str) -> None:
    client = db.get(Client, message.client_pk)
    if client is None:
        return
    label = CHANNEL_LABEL[message.channel]
    notify(
        db,
        kind=NotificationKind.CHANNEL_FAILED,
        dedupe_key=f"channel:{message.id}",
        client_pk=client.id,
        cycle_id=message.cycle_id,
        user_id=client.owner_id,
        title=f"{label} to {client.legal_name} failed",
        body=f"The {label} message could not be sent ({error[:200]}). The email went as usual; "
        "call the client if they may not have seen it.",
    )


def dispatch(
    factory: sessionmaker[Session],
    settings: Settings,
    provider: MessagingProvider | None = None,
) -> DispatchTally:
    """Send every queued message. Run by the alerts job, in its lock and sending hours."""
    tally = DispatchTally()
    provider = provider or provider_from_settings(settings)
    with factory() as db:
        ids = list(
            db.scalars(
                select(ChannelMessage.id)
                .where(ChannelMessage.status == MessageStatus.QUEUED)
                .order_by(ChannelMessage.id)
                .limit(1000)
            )
        )
    for message_id in ids:
        with factory() as db:
            message = db.scalar(
                select(ChannelMessage)
                .where(
                    ChannelMessage.id == message_id,
                    ChannelMessage.status == MessageStatus.QUEUED,
                )
                .with_for_update()
            )
            if message is None:
                continue
            outcome = _send_one(db, settings, provider, message)
            setattr(tally, outcome, getattr(tally, outcome) + 1)
            if outcome == "failed":
                tally.failures.append(f"message {message.id}: {message.last_error}")
            db.commit()
    if tally.sent or tally.failed or tally.skipped:
        log.info(
            "whatsapp and sms",
            extra={"ctx": {"sent": tally.sent, "failed": tally.failed, "skipped": tally.skipped}},
        )
    return tally


def _send_one(
    db: Session, settings: Settings, provider: MessagingProvider | None, message: ChannelMessage
) -> str:
    contact = db.get(Contact, message.contact_id)
    # Checked again now: the client may have opted out, or the number gone, since it was queued.
    if contact is None or not contact.phone or not consent_of(db, contact.id)[message.channel]:
        _finish(message, MessageStatus.SKIPPED, "consent withdrawn or no number")
        audit(db, "message.skipped", target=("channel_message", message.id))
        return "skipped"
    if provider is None or message.template not in settings.messaging_approved_templates:
        _finish(message, MessageStatus.SKIPPED, "second channel switched off")
        return "skipped"
    message.attempts += 1
    try:
        provider_id = provider.send(
            OutgoingMessage(
                reference=f"ll-msg-{message.id}",
                channel=message.channel,
                to=contact.phone,
                template=message.template,
                params=tuple(message.params),
                text=message.body_text,
            )
        )
    except TemporaryFailure as exc:
        if message.attempts < MAX_ATTEMPTS:
            message.last_error = str(exc)[:300]
            return "retrying"
        _finish(message, MessageStatus.FAILED, str(exc))
    except PermanentFailure as exc:
        _finish(message, MessageStatus.FAILED, str(exc))
    else:
        message.provider_id = provider_id[:100]
        _finish(message, MessageStatus.SENT, None)
        audit(
            db,
            "message.sent",
            target=("channel_message", message.id),
            details={"channel": message.channel.value, "template": message.template},
        )
        return "sent"
    _tell_owner(db, message, message.last_error or "unknown error")
    audit(
        db,
        "message.failed",
        target=("channel_message", message.id),
        details={"channel": message.channel.value, "attempts": message.attempts},
    )
    return "failed"
