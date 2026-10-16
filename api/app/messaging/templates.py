"""The WhatsApp and SMS message templates (BRD C-2).

WhatsApp only delivers business-started messages that match a template the provider approved,
and Indian SMS needs templates registered on DLT. So the texts are fixed here, short (two SMS
parts at most), and carry no private link or personal identifier: the link is in the email, which
always goes too. A template is used only once its key is listed in
`LL_MESSAGING_APPROVED_TEMPLATES`, after the provider has approved it word for word.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from app.mail.templates import format_day

MAX_LENGTH = 300
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class MessageTemplate:
    key: str
    text: str

    @property
    def params(self) -> tuple[str, ...]:
        """The values in order, as a WhatsApp template's {{1}}, {{2}}..."""
        return tuple(_PLACEHOLDER.findall(self.text))


TEMPLATES: dict[str, MessageTemplate] = {
    t.key: t
    for t in (
        MessageTemplate(
            "first_alert",
            "{{firm_name}}: we need documents for {{client_name}}'s {{filing}} for {{period}}, "
            "due {{due_date}}. We have emailed you the list and a private link to upload "
            "them. Ref {{ticket_id}}.",
        ),
        MessageTemplate(
            "reminder",
            "{{firm_name}}: a reminder that documents for {{client_name}}'s {{filing}} for "
            "{{period}} are still needed (due {{due_date}}). Please see our email for the "
            "upload link. Ref {{ticket_id}}.",
        ),
    )
}


class MessageTemplateError(ValueError):
    pass


def render_message(key: str, values: Mapping[str, str | int | date]) -> tuple[str, tuple[str, ...]]:
    """The text and the ordered parameter values. Every placeholder needs a value."""
    template = TEMPLATES[key]
    params: list[str] = []
    for name in template.params:
        if name not in values:
            raise MessageTemplateError(f"{key}: no value for {name}")
        value = values[name]
        text = format_day(value) if isinstance(value, date) else str(value)
        params.append(" ".join(text.split())[:120])  # one line each, as templates require
    it = iter(params)
    body = _PLACEHOLDER.sub(lambda _: next(it), template.text)
    return body[:MAX_LENGTH], tuple(params)
