"""Password rules and hashing: Argon2id, length-based policy, no composition rules (NIST 800-63B)."""

from __future__ import annotations

import secrets
import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 128

_hasher = PasswordHasher()  # Argon2id with the library's current recommended cost settings

# Long-enough passwords that are still among the first ones an attacker tries.
_COMMON = frozenset(
    {
        "passwordpassword",
        "password1234",
        "password12345",
        "123456789012",
        "1234567890123",
        "qwertyuiop12",
        "qwertyuiopas",
        "iloveyou1234",
        "administrator",
        "administrator1",
        "welcome12345",
        "letmein12345",
        "changeme1234",
        "abcdefghijkl",
        "abcd12345678",
    }
)
_TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alikes (0 o 1 l i)


def _norm(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


def check_policy(
    password: str, *, email: str | None = None, full_name: str | None = None
) -> list[str]:
    """Return plain-language problems; an empty list means the password is acceptable."""
    pw = _norm(password)
    problems: list[str] = []
    if len(pw) < MIN_LENGTH:
        problems.append(f"Use at least {MIN_LENGTH} characters.")
    if len(pw) > MAX_LENGTH:
        problems.append(f"Use at most {MAX_LENGTH} characters.")
    lowered = pw.lower()
    if len(set(lowered)) < 5 and len(pw) >= MIN_LENGTH:
        problems.append("Use a more varied password.")
    if lowered in _COMMON or "ledgerline" in lowered:
        problems.append("That password is too easy to guess.")
    if email:
        local = email.split("@", 1)[0].lower()
        if len(local) >= 4 and local in lowered:
            problems.append("Do not include your email name in the password.")
    if full_name:
        for part in full_name.lower().split():
            if len(part) >= 4 and part in lowered:
                problems.append("Do not include your name in the password.")
                break
    return problems


def hash_password(password: str) -> str:
    return _hasher.hash(_norm(password))


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, _norm(password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


_DUMMY_HASH = _hasher.hash("not-a-real-password-used-only-for-timing")


def burn_time(password: str) -> None:
    """Spend the same effort as a real check, so unknown accounts are not faster to reject."""
    verify_password(_DUMMY_HASH, password)


def generate_temporary_password() -> str:
    """Shown once to the Administrator. Four groups of four, e.g. `k7mp-x3qa-8hrw-n4te`."""
    groups = ["".join(secrets.choice(_TEMP_ALPHABET) for _ in range(4)) for _ in range(4)]
    return "-".join(groups)
