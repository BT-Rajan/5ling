"""Which files may be uploaded (BRD FR-AL-04): checked by content, not by name alone.

A file's extension must be on the list and its first bytes must match that kind of file, so a
renamed executable is refused. Office files with macros are refused (by extension, and for the
newer formats by looking inside for a macro project). The name is cleaned before it is kept.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass


@dataclass(frozen=True)
class Kind:
    extension: str
    content_type: str
    label: str


KINDS: dict[str, Kind] = {
    k.extension: k
    for k in (
        Kind("pdf", "application/pdf", "PDF"),
        Kind("jpg", "image/jpeg", "JPEG image"),
        Kind("jpeg", "image/jpeg", "JPEG image"),
        Kind("png", "image/png", "PNG image"),
        Kind("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "Excel"),
        Kind("xls", "application/vnd.ms-excel", "Excel (old format)"),
        Kind(
            "docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "Word",
        ),
        Kind("csv", "text/csv", "CSV"),
        Kind("txt", "text/plain", "text"),
    )
}

ALLOWED_TEXT = ", ".join(sorted({k.upper() for k in KINDS if k != "jpeg"}))
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP = b"PK\x03\x04"
MAX_NAME = 150


class FileRefused(ValueError):
    """Why a file cannot be accepted, in words for the person uploading it."""


def clean_name(name: str) -> str:
    """A safe display name: no folders, control characters or odd Unicode tricks."""
    name = unicodedata.normalize("NFKC", name).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if unicodedata.category(c)[0] not in {"C"})
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        raise FileRefused("The file needs a name.")
    if len(name) > MAX_NAME:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: MAX_NAME - len(ext) - 1] + dot + ext) if dot else name[:MAX_NAME]
    return name


def _office_ok(data: bytes, extension: str) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
    except (zipfile.BadZipFile, ValueError):
        return False
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise FileRefused("Files with macros are not accepted. Save it without macros first.")
    marker = "xl/workbook.xml" if extension == "xlsx" else "word/document.xml"
    return "[Content_Types].xml" in names and marker in names


def _text_ok(data: bytes) -> bool:
    if b"\0" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def check(name: str, data: bytes, max_bytes: int) -> tuple[str, Kind]:
    """The cleaned name and the file's kind, or FileRefused with the reason."""
    name = clean_name(name)
    if not data:
        raise FileRefused("The file is empty.")
    if len(data) > max_bytes:
        raise FileRefused(f"The file is larger than {max_bytes // (1024 * 1024)} MB.")
    extension = name.rpartition(".")[2].lower() if "." in name else ""
    if extension in {"xlsm", "xltm", "docm", "dotm", "xlsb"}:
        raise FileRefused("Files with macros are not accepted. Save it without macros first.")
    kind = KINDS.get(extension)
    if kind is None:
        raise FileRefused(f"This kind of file is not accepted. Use {ALLOWED_TEXT}.")
    matches = {
        "pdf": data.startswith(b"%PDF-"),
        "jpg": data.startswith(b"\xff\xd8\xff"),
        "jpeg": data.startswith(b"\xff\xd8\xff"),
        "png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "xls": data.startswith(_OLE),
        "csv": _text_ok(data),
        "txt": _text_ok(data),
    }.get(extension)
    if matches is None:  # xlsx, docx
        matches = data.startswith(_ZIP) and _office_ok(data, extension)
    if not matches:
        raise FileRefused(f"This file is not really a {kind.label} file.")
    return name, kind
