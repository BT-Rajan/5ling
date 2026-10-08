"""JSON logging that never writes personal identifiers or secrets (BRD NFR-SE-06).

Redaction runs on the final text of every record, including exception tracebacks, and is
attached to the handler so it covers every logger (ours, uvicorn, libraries).
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("secret", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("secret", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    (
        "secret",
        re.compile(
            r"(?i)\b(password|passwd|secret|token|authorization|api[_-]?key|otp)\b"
            r"(\"?\s*[:=]\s*\"?)[^\s,\"'}&]+"
        ),
    ),
    ("gstin", re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")),
    ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")),
    # 9-18 digits, optionally grouped with single spaces or hyphens: bank accounts, Aadhaar, phones
    ("number", re.compile(r"(?<![\w-])\d(?:[ -]?\d){8,17}(?![\w-])")),
]


def redact(text: str) -> str:
    for label, pattern in _RULES:
        if label == "secret" and pattern.groups == 2:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        else:
            text = pattern.sub(f"[REDACTED:{label}]", text)
    return text


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            for key, value in extra.items():
                entry[str(key)] = redact(value) if isinstance(value, str) else value
        if record.exc_info:
            entry["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingJsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # uvicorn installs its own handlers; route them through ours.
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
    # Uvicorn's access log prints the full URL including query strings. We log requests ourselves.
    logging.getLogger("uvicorn.access").disabled = True
