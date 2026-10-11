"""Two-step sign-in: TOTP codes (RFC 6238), encrypted storage of the secret, recovery codes.

TOTP is implemented here directly (HMAC-SHA1, 6 digits, 30 seconds) so it can be read and tested
against the RFC's own vectors, and so it works with every common authenticator app.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import struct
from datetime import datetime
from urllib.parse import quote

import segno
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ISSUER = "Ledgerline"
DIGITS = 6
PERIOD = 30
WINDOW = 1  # accept the previous and next 30-second code too, for slow phone clocks
RECOVERY_CODE_COUNT = 10

_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alikes (0 o 1 l i)
_FORMAT_V1 = "v1:"


# ---- TOTP ----------------------------------------------------------------------------------


def new_secret() -> str:
    """160 random bits, base32 (the form authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret_b32: str, counter: int, digits: int = DIGITS) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(number).zfill(digits)


def time_step(when: datetime) -> int:
    return int(when.timestamp()) // PERIOD


def totp_at(secret_b32: str, when: datetime) -> str:
    return _hotp(secret_b32, time_step(when))


def normalise_code(code: str) -> str:
    return re.sub(r"[\s-]", "", code)


def _is_six_digits(code: str) -> bool:
    # [0-9], not \d: \d also matches digits from other scripts, which the comparison cannot handle.
    return re.fullmatch(r"[0-9]{6}", code) is not None


def looks_like_totp(code: str) -> bool:
    return _is_six_digits(normalise_code(code))


def verify_totp(secret_b32: str, code: str, now: datetime, last_step: int | None) -> int | None:
    """The time-step the code belongs to, or None. A step at or before `last_step` is a replay."""
    code = normalise_code(code)
    if not _is_six_digits(code):
        return None
    current = time_step(now)
    matched: int | None = None
    for step in range(current - WINDOW, current + WINDOW + 1):
        # Check every step (no early exit) so timing does not reveal which one matched.
        if hmac.compare_digest(_hotp(secret_b32, step), code) and (
            last_step is None or step > last_step
        ):
            matched = step
    return matched


def otpauth_uri(secret_b32: str, email: str) -> str:
    label = f"{quote(ISSUER)}:{quote(email)}"
    return (
        f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(ISSUER)}"
        f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}"
    )


def qr_data_uri(uri: str) -> str:
    """A scannable image as a data: URI. Always black on white so every scanner reads it."""
    return segno.make(uri, error="m").svg_data_uri(
        scale=6, border=2, dark="#000000", light="#ffffff"
    )


def group_secret(secret_b32: str) -> str:
    return " ".join(secret_b32[i : i + 4] for i in range(0, len(secret_b32), 4))


# ---- keys and storage -----------------------------------------------------------------------


def _derive(secret_key: str, purpose: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose).derive(
        secret_key.encode()
    )


def _fernet(secret_key: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_derive(secret_key, b"ledgerline/mfa/totp-secret/v1")))


def encrypt_secret(plain: str, secret_key: str) -> str:
    """Authenticator secrets must be readable to check codes, so they are encrypted, not hashed."""
    return _FORMAT_V1 + _fernet(secret_key).encrypt(plain.encode()).decode()


def decrypt_secret(stored: str | None, secret_key: str) -> str | None:
    if not stored or not stored.startswith(_FORMAT_V1):
        return None
    try:
        return _fernet(secret_key).decrypt(stored[len(_FORMAT_V1) :].encode()).decode()
    except InvalidToken:
        return None


# ---- recovery codes -------------------------------------------------------------------------


def new_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    def one() -> str:
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        return f"{raw[:5]}-{raw[5:]}"

    return [one() for _ in range(count)]


def normalise_recovery(code: str) -> str:
    return re.sub(r"[^a-z0-9]", "", code.lower())


def recovery_hash(code: str, secret_key: str) -> str:
    key = _derive(secret_key, b"ledgerline/mfa/recovery-code/v1")
    return hmac.new(key, normalise_recovery(code).encode(), hashlib.sha256).hexdigest()
