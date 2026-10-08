from __future__ import annotations

import io
import json
import logging

import pytest
from app.logging_conf import RedactingJsonFormatter, redact, setup_logging


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        ("pan ABCDE1234F filed", "pan"),
        ("gstin 27ABCDE1234F1Z5 ok", "gstin"),
        ("mail ca.partner@firm.example.com sent", "email"),
        ("account 123456789012 debited", "number"),
        ("aadhaar 1234 5678 9012", "number"),
        ("phone 9876543210", "number"),
    ],
)
def test_identifiers_are_redacted(raw: str, label: str) -> None:
    out = redact(raw)
    assert f"[REDACTED:{label}]" in out
    for secret in (
        "ABCDE1234F",
        "27ABCDE1234F1Z5",
        "firm.example.com",
        "123456789012",
        "5678 9012",
        "9876543210",
    ):
        assert secret not in out


def test_tokens_and_passwords_redacted() -> None:
    assert "hunter2" not in redact("login password=hunter2 failed")
    assert "abc123def456" not in redact("Authorization: Bearer abc123def456ghi")
    assert "ghp_" not in redact("token ghp_" + "A" * 36)
    assert "s3cr3tvalue" not in redact('{"api_key": "s3cr3tvalue"}')


def test_ordinary_text_and_six_digit_client_ids_survive() -> None:
    text = "client 100001 moved to Assigned for TKT-000123 on 2026-10-08"
    assert redact(text) == text


def test_gstin_wins_over_pan_inside_it() -> None:
    assert "[REDACTED:gstin]" in redact("27ABCDE1234F1Z5")


def test_formatter_redacts_message_context_and_tracebacks() -> None:
    fmt = RedactingJsonFormatter()
    try:
        raise ValueError("bad PAN ABCDE1234F for a@b.co")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "t", logging.ERROR, "f", 1, "msg %s", ("ABCDE1234F",), sys.exc_info()
        )
    record.ctx = {"owner": "a@b.co", "status": 200}
    entry = json.loads(fmt.format(record))
    blob = json.dumps(entry)
    assert "ABCDE1234F" not in blob
    assert "a@b.co" not in blob
    assert entry["status"] == 200


def test_every_logger_including_uvicorn_goes_through_redaction(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging("INFO")
    logging.getLogger("some.library").warning("saw ABCDE1234F")
    logging.getLogger("uvicorn.error").warning("saw ABCDE1234F")
    out = capsys.readouterr().out
    assert "ABCDE1234F" not in out
    assert out.count("[REDACTED:pan]") == 2
    assert logging.getLogger("uvicorn.access").disabled is True


def test_stream_is_valid_json_lines(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("INFO")
    logging.getLogger("x").info("hello")
    for line in io.StringIO(capsys.readouterr().out):
        assert json.loads(line)["msg"] == "hello"
