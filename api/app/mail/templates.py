"""Email templates (CR-03): fixed text with named blanks, filled in from plain values.

Deliberately not a general template language: a template can only name a blank from its own
list, so nothing in a template or a value is ever executed, and every placeholder has to be
filled (a missing value is an error, not an empty gap in an email to a client). Emails are plain
text, so values need no HTML escaping. Line breaks are removed from the subject, which becomes
an email header.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
MAX_VALUE = 2000
MAX_SUBJECT = 250
MAX_BODY = 20_000

# Blanks a template may use, with what each means.
VARIABLES: dict[str, str] = {
    "firm_name": "The firm's name",
    "client_name": "The client's legal name",
    "client_id": "The 6-digit client ID",
    "ticket_id": "The ticket ID, e.g. TKT-000123",
    "filing": "The filing, e.g. GSTR-1",
    "period": "The period, e.g. Apr 2026",
    "stage": "The cycle's stage, or the client's status",
    "previous_stage": "The stage or status before the change",
    "due_date": "The due date, e.g. Mon 11 May 2026",
    "note": "A note from the sender (may be empty)",
    "sender_name": "Who is sending it",
    "detail": "Details supplied by the system",
    "link": "A private link to the client portal",
    "expires": "When the link stops working, India time",
}


@dataclass(frozen=True)
class Template:
    key: str
    subject: str
    body: str

    def blanks(self) -> set[str]:
        return set(PLACEHOLDER.findall(self.subject)) | set(PLACEHOLDER.findall(self.body))


@dataclass(frozen=True)
class Rendered:
    subject: str
    body: str


class TemplateError(ValueError):
    pass


TEMPLATES: dict[str, Template] = {
    t.key: t
    for t in (
        Template(
            "test",
            "Test email from {{firm_name}}",
            "This is a test email from {{firm_name}}, sent by {{sender_name}}.\n\n"
            "If you received it, email from Ledgerline is working.\n",
        ),
        Template(
            "audit_alert",
            "Action needed: the {{firm_name}} audit log check failed",
            "The audit log check failed.\n\n{{detail}}\n\n"
            "Sign in to Ledgerline and open the Audit log page for details. Until this is "
            "resolved, treat the audit log as possibly altered.\n",
        ),
        Template(
            "stage_change",
            "{{filing}} {{period}}: {{stage}} ({{ticket_id}})",
            "Dear {{client_name}},\n\n"
            "Your {{filing}} for {{period}} has moved from {{previous_stage}} to {{stage}}.\n"
            "Due date: {{due_date}}.\n\n"
            "{{note}}\n\n"
            "Reference: {{ticket_id}}, client ID {{client_id}}.\n\n"
            "{{sender_name}}\n{{firm_name}}\n",
        ),
        Template(
            "portal_link",
            "{{filing}} {{period}}: your private link ({{ticket_id}})",
            "Dear {{client_name}},\n\n"
            "Here is your private link for your {{filing}} for {{period}}, due {{due_date}}:\n\n"
            "{{link}}\n\n"
            "It opens once, on one device, and works until {{expires}}. Please do not forward "
            "it. If it no longer works, open it anyway and ask for a new one: we will email it "
            "to this address.\n\n"
            "Reference: {{ticket_id}}, client ID {{client_id}}.\n\n"
            "{{firm_name}}\n",
        ),
        Template(
            "task_status_change",
            "{{detail}}: {{stage}} ({{ticket_id}})",
            "Dear {{client_name}},\n\n"
            '"{{detail}}" has moved from {{previous_stage}} to {{stage}}.\n\n'
            "{{note}}\n\n"
            "Reference: {{ticket_id}}, client ID {{client_id}}.\n\n"
            "{{sender_name}}\n{{firm_name}}\n",
        ),
        Template(
            "client_status_change",
            "Your account with {{firm_name}} is now {{stage}} (client ID {{client_id}})",
            "Dear {{client_name}},\n\n"
            "Your account status has changed from {{previous_stage}} to {{stage}}.\n\n"
            "{{note}}\n\n"
            "Client ID: {{client_id}}.\n\n"
            "{{sender_name}}\n{{firm_name}}\n",
        ),
    )
}


def check_template(template: Template) -> None:
    unknown = template.blanks() - VARIABLES.keys()
    if unknown:
        raise TemplateError(f"unknown blanks in {template.key}: {', '.join(sorted(unknown))}")


for _t in TEMPLATES.values():
    check_template(_t)


def format_day(value: date) -> str:
    """Mon 11 May 2026: the same style as the screens."""
    return f"{value:%a} {value.day} {value:%b %Y}"


def _fill(text: str, values: Mapping[str, str]) -> str:
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], text)


def render(key: str, values: Mapping[str, str | int | date | None]) -> Rendered:
    """Fill the template's blanks. Every blank it uses must be given (None counts as empty)."""
    template = TEMPLATES.get(key)
    if template is None:
        raise TemplateError(f"no template called {key}")
    missing = template.blanks() - values.keys()
    if missing:
        raise TemplateError(f"{key} needs {', '.join(sorted(missing))}")
    clean: dict[str, str] = {}
    for name in template.blanks():
        value = values[name]
        if value is None:
            text = ""
        elif isinstance(value, date):
            text = format_day(value)
        else:
            text = str(value)
        clean[name] = text.replace("\r\n", "\n").replace("\r", "\n")[:MAX_VALUE].strip()
    subject = " ".join(_fill(template.subject, clean).split())[:MAX_SUBJECT]
    body = re.sub(r"\n{3,}", "\n\n", _fill(template.body, clean)).strip() + "\n"
    if len(body) > MAX_BODY:
        raise TemplateError("the email is too long")
    return Rendered(subject=subject, body=body)
