"""How email leaves the system (BRD INT-01): one small interface, SMTP behind it.

SMTP covers Mailpit in development and every mainstream provider's relay in production. A
provider's HTTP API can be added behind the same `EmailProvider` interface without touching the
outbox. Delivery and bounce reports come back separately, through /api/email/events.

A send either returns the provider's message ID or raises:
- `TemporaryFailure`: try again later (connection trouble, 4xx replies);
- `PermanentFailure`: do not retry (5xx replies, every recipient refused).
"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass, field
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from typing import Protocol

from app.config import Settings
from app.timeutil import utcnow


@dataclass(frozen=True)
class Outgoing:
    public_id: str
    to: list[str]
    cc: list[str]
    subject: str
    body: str


class TemporaryFailure(Exception):
    pass


class PermanentFailure(Exception):
    pass


class EmailProvider(Protocol):
    def send(self, message: Outgoing) -> str: ...


def message_id_for(public_id: str, from_address: str) -> str:
    """Our own Message-ID, so delivery reports can be matched to the outbox row."""
    domain = from_address.rsplit("@", 1)[-1]
    return make_msgid(idstring=public_id, domain=domain)


def build_message(
    message: Outgoing, *, from_address: str, from_name: str
) -> tuple[EmailMessage, str]:
    msg = EmailMessage()
    msg["From"] = Address(display_name=from_name, addr_spec=from_address)
    msg["To"] = ", ".join(message.to)
    if message.cc:
        msg["Cc"] = ", ".join(message.cc)
    # The header API refuses CR or LF in a value, so a subject cannot add headers.
    msg["Subject"] = message.subject
    msg["Date"] = format_datetime(utcnow())
    message_id = message_id_for(message.public_id, from_address)
    msg["Message-ID"] = message_id
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(message.body, charset="utf-8")
    return msg, message_id


@dataclass
class SmtpProvider:
    host: str
    port: int
    security: str  # none, starttls or tls
    username: str | None
    password: str | None
    timeout: int
    from_address: str
    from_name: str

    def _connect(self) -> smtplib.SMTP:
        context = ssl.create_default_context()
        if self.security == "tls":
            return smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context)
        client = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        if self.security == "starttls":
            client.starttls(context=context)
        return client

    def send(self, message: Outgoing) -> str:
        msg, message_id = build_message(
            message, from_address=self.from_address, from_name=self.from_name
        )
        try:
            with self._connect() as client:
                if self.username:
                    client.login(self.username, self.password or "")
                # Recipients refused while others were accepted come back here. It still counts
                # as sent: sending again would duplicate it for everyone else.
                client.send_message(msg)
        except smtplib.SMTPRecipientsRefused as exc:
            codes = sorted({code for code, _ in exc.recipients.values()})
            text = f"every recipient was refused ({', '.join(map(str, codes))})"
            if any(400 <= code < 500 for code in codes):
                raise TemporaryFailure(text) from exc  # "try later" for at least one
            raise PermanentFailure(text) from exc
        except smtplib.SMTPResponseException as exc:
            text = f"the mail server replied {exc.smtp_code}"
            if 500 <= exc.smtp_code < 600:
                raise PermanentFailure(text) from exc
            raise TemporaryFailure(text) from exc
        except (smtplib.SMTPException, OSError) as exc:  # includes timeouts and TLS errors
            raise TemporaryFailure(
                f"could not reach the mail server ({type(exc).__name__})"
            ) from exc
        return message_id


@dataclass
class RecordingProvider:
    """For tests and dry runs: keeps what would have been sent, or fails on request."""

    sent: list[Outgoing] = field(default_factory=list)
    fail_with: Exception | None = None

    def send(self, message: Outgoing) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(message)
        return f"<{message.public_id}@recorded>"


def provider_from_settings(settings: Settings) -> EmailProvider:
    return SmtpProvider(
        host=settings.smtp_host,
        port=settings.smtp_port,
        security=settings.smtp_security,
        username=settings.smtp_username,
        password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
        timeout=settings.smtp_timeout_seconds,
        from_address=settings.email_from_address,
        from_name=settings.email_from_name,
    )
