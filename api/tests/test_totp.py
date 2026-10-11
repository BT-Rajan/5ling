"""TOTP and recovery codes, checked against RFC 6238's own test vectors."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from app.auth import mfa

KEY = "9f2c6a1be07d4835a6c1f0e29b7d3c58e41a0f6b"
# RFC 6238 appendix B: the shared secret is the ASCII string "12345678901234567890".
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


@pytest.mark.parametrize(
    ("unix_time", "expected8"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_matches_the_rfc_6238_vectors(unix_time: int, expected8: str) -> None:
    step = unix_time // 30
    assert mfa._hotp(RFC_SECRET, step, digits=8) == expected8
    # The six-digit code is the last six digits of the eight-digit one.
    assert mfa._hotp(RFC_SECRET, step) == expected8[-6:]


def _at(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def test_the_code_changes_every_thirty_seconds() -> None:
    start = 1111111110  # exactly on a 30-second boundary
    assert mfa.totp_at(RFC_SECRET, _at(start)) == mfa.totp_at(RFC_SECRET, _at(start + 29))
    assert mfa.totp_at(RFC_SECRET, _at(start)) != mfa.totp_at(RFC_SECRET, _at(start + 30))


def test_neighbouring_codes_are_accepted_but_not_older_or_newer() -> None:
    now = _at(1111111109)
    for offset, accepted in [(-2, False), (-1, True), (0, True), (1, True), (2, False)]:
        code = mfa.totp_at(RFC_SECRET, now + timedelta(seconds=30 * offset))
        assert (mfa.verify_totp(RFC_SECRET, code, now, None) is not None) is accepted, offset


def test_a_code_cannot_be_used_twice() -> None:
    now = _at(1111111109)
    code = mfa.totp_at(RFC_SECRET, now)
    step = mfa.verify_totp(RFC_SECRET, code, now, None)
    assert step == 1111111109 // 30
    assert mfa.verify_totp(RFC_SECRET, code, now, step) is None  # replay
    older = mfa.totp_at(RFC_SECRET, now - timedelta(seconds=30))
    assert mfa.verify_totp(RFC_SECRET, older, now, step) is None  # an earlier step is also refused
    newer = mfa.totp_at(RFC_SECRET, now + timedelta(seconds=30))
    assert mfa.verify_totp(RFC_SECRET, newer, now, step) == step + 1


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "12345",
        "1234567",
        "abcdef",
        "12 34",
        "\uff11\uff12\uff13\uff14\uff15\uff16",
        "\u0661\u0662\u0663\u0664\u0665\u0666",
    ],
)
def test_malformed_codes_are_refused(bad: str) -> None:
    assert mfa.verify_totp(RFC_SECRET, bad, _at(1111111109), None) is None


def test_spaces_and_hyphens_in_a_typed_code_are_tolerated() -> None:
    now = _at(1111111109)
    code = mfa.totp_at(RFC_SECRET, now)
    assert mfa.verify_totp(RFC_SECRET, f"{code[:3]} {code[3:]}", now, None) is not None
    assert mfa.verify_totp(RFC_SECRET, f"{code[:3]}-{code[3:]}", now, None) is not None


def test_new_secrets_are_160_bits_of_base32_and_unique() -> None:
    secrets_seen = {mfa.new_secret() for _ in range(50)}
    assert len(secrets_seen) == 50
    for s in secrets_seen:
        assert len(s) == 32
        assert len(base64.b32decode(s)) == 20


def test_stored_secret_is_encrypted_and_round_trips() -> None:
    plain = mfa.new_secret()
    stored = mfa.encrypt_secret(plain, KEY)
    assert stored.startswith("v1:")
    assert plain not in stored
    assert mfa.decrypt_secret(stored, KEY) == plain
    assert mfa.encrypt_secret(plain, KEY) != stored  # fresh nonce each time


def test_wrong_key_or_tampering_or_junk_gives_nothing() -> None:
    stored = mfa.encrypt_secret(mfa.new_secret(), KEY)
    assert mfa.decrypt_secret(stored, "another-key-" + KEY) is None
    assert mfa.decrypt_secret(stored[:-4] + "AAAA", KEY) is None
    assert mfa.decrypt_secret("v2:whatever", KEY) is None
    assert mfa.decrypt_secret("plain-secret", KEY) is None
    assert mfa.decrypt_secret(None, KEY) is None
    assert mfa.decrypt_secret("", KEY) is None


def test_authenticator_link_and_qr() -> None:
    uri = mfa.otpauth_uri("JBSWY3DPEHPK3PXP", "asha.rao+ca@firm.in")
    assert uri.startswith("otpauth://totp/Ledgerline:asha.rao%2Bca%40firm.in?")
    for part in (
        "secret=JBSWY3DPEHPK3PXP",
        "issuer=Ledgerline",
        "digits=6",
        "period=30",
        "algorithm=SHA1",
    ):
        assert part in uri
    qr = mfa.qr_data_uri(uri)
    assert qr.startswith("data:image/svg+xml")
    assert "JBSWY3DPEHPK3PXP" not in qr  # the image encodes it; the markup does not spell it out


def test_secret_is_shown_in_groups_of_four() -> None:
    assert mfa.group_secret("JBSWY3DPEHPK3PXP") == "JBSW Y3DP EHPK 3PXP"


def test_recovery_codes_are_unique_well_formed_and_unambiguous() -> None:
    codes = mfa.new_recovery_codes()
    assert len(codes) == 10
    assert len(set(codes)) == 10
    for code in codes:
        assert len(code) == 11
        assert code[5] == "-"
        assert not set(code.replace("-", "")) & set("0o1li")


def test_recovery_hashes_ignore_case_and_punctuation_and_depend_on_the_key() -> None:
    h = mfa.recovery_hash("abcde-fghjk", KEY)
    assert h == mfa.recovery_hash("ABCDE FGHJK", KEY)
    assert h == mfa.recovery_hash("abcdefghjk", KEY)
    assert h != mfa.recovery_hash("abcde-fghjm", KEY)
    assert h != mfa.recovery_hash("abcde-fghjk", "different-" + KEY)
    assert len(h) == 64
