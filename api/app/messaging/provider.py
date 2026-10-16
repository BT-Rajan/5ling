"""The WhatsApp or SMS provider behind one small interface (BRD INT-02, open question Q2).

The provider is not chosen yet (Q2), so this holds the interface, a log-only provider for testing
an installation, and a recording one for tests. A real provider is one class with `send`, added
here and selected by `LL_MESSAGING_PROVIDER`; nothing else changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from app.config import Settings
from app.mail.provider import PermanentFailure, TemporaryFailure
from app.models import Channel

__all__ = [
    "LogOnlyProvider",
    "MessagingProvider",
    "OutgoingMessage",
    "PermanentFailure",
    "RecordingProvider",
    "TemporaryFailure",
    "provider_from_settings",
]

log = logging.getLogger("ledgerline.messaging")


@dataclass(frozen=True)
class OutgoingMessage:
    reference: str  # our id, so a retried send can be recognised by the provider
    channel: Channel
    to: str  # E.164, e.g. +919876543210
    template: str  # the approved template's key
    params: tuple[str, ...]  # the template's values, in order
    text: str  # the same message as plain text (SMS, and the record)


class MessagingProvider(Protocol):
    def send(self, message: OutgoingMessage) -> str:
        """Send it and return the provider's id. Raise TemporaryFailure to have it tried again,
        PermanentFailure if it never can be (a bad number, an unapproved template)."""
        ...


class LogOnlyProvider:
    """Records that a message would have gone, without sending (LL_MESSAGING_PROVIDER=log)."""

    def send(self, message: OutgoingMessage) -> str:
        log.info(
            "message not sent (log-only provider)",
            extra={
                "ctx": {
                    "reference": message.reference,
                    "channel": message.channel.value,
                    "template": message.template,
                    "to_last4": message.to[-4:],
                }
            },
        )
        return f"log-{message.reference}"


@dataclass
class RecordingProvider:
    """For tests: keeps what was sent, or fails as told."""

    fail_with: Exception | None = None
    sent: list[OutgoingMessage] = field(default_factory=list)

    def send(self, message: OutgoingMessage) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(message)
        return f"rec-{len(self.sent)}"


def provider_from_settings(settings: Settings) -> MessagingProvider | None:
    """None when the second channel is switched off (the default until Q2 is answered)."""
    if settings.messaging_provider == "log":
        return LogOnlyProvider()
    return None
