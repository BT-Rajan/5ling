"""Indian tax identifiers (BRD FR-CL-01): PAN and GSTIN, checked before a client is saved.

Values here are personal identifiers: never log them (NFR-SE-06), and never put them in audit
details (the redactor would strip them anyway).
"""

from __future__ import annotations

import re

# PAN: 5 letters, 4 digits, 1 letter. The 4th letter says who holds it.
_PAN = re.compile(r"^[A-Z]{3}[ABCFGHJLPT][A-Z][0-9]{4}[A-Z]$")
PAN_HOLDERS = {
    "P": "individual",
    "C": "company",
    "H": "Hindu undivided family",
    "F": "firm or LLP",
    "A": "association of persons",
    "T": "trust",
    "B": "body of individuals",
    "L": "local authority",
    "J": "artificial juridical person",
    "G": "government",
}

# GSTIN: 2-digit state code, the holder's PAN, an entity number, a letter (usually Z), a check
# character computed from the first 14.
_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z][A-Z0-9][0-9A-Z]$")
_STATE_CODES = frozenset({f"{n:02d}" for n in range(1, 39)} | {"97", "99"})
_BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def tidy(value: str) -> str:
    """Upper case, with spaces and hyphens people type removed."""
    return re.sub(r"[\s-]", "", value).upper()


def pan_problem(pan: str) -> str | None:
    """None if `pan` (already tidied) is well formed, else why not."""
    if len(pan) != 10:
        return "A PAN has 10 characters, like ABCPE1234F."
    if not _PAN.match(pan):
        return "That is not a valid PAN. It should look like ABCPE1234F."
    return None


def gstin_check_char(first14: str) -> str:
    """The GSTIN check character (mod 36, alternating weights 1 and 2)."""
    total = 0
    for i, char in enumerate(first14):
        product = _BASE36.index(char) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _BASE36[(36 - total % 36) % 36]


def gstin_problem(gstin: str) -> str | None:
    if len(gstin) != 15:
        return "A GSTIN has 15 characters, like 27ABCPE1234F1Z5."
    if not _GSTIN.match(gstin):
        return "That is not a valid GSTIN."
    if gstin[:2] not in _STATE_CODES:
        return "That GSTIN does not start with a valid state code."
    if gstin_check_char(gstin[:14]) != gstin[14]:
        return "That GSTIN's last character does not match; check it for a typing error."
    return None


def pan_in_gstin(gstin: str) -> str:
    return gstin[2:12]
