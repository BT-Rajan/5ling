"""Where document files live (BRD INT-04, NFR-SE-04, NFR-SE-05).

Every file is encrypted here, before any backend sees it, so the backend (a local disk today,
S3-compatible object storage later) only ever holds ciphertext:

- each file gets its own random 256-bit key and is sealed with AES-256-GCM;
- that key is itself sealed with the master key (LL_STORAGE_KEY), which is kept apart from
  the files and the database (NFR-SE-04: keys held separately from data);
- both seals name the file's storage key as associated data, so one stored file cannot be
  passed off as another.

Files are written once and never overwritten (a second write to the same name fails). There is
no public address: files are read only by the API after an access check (NFR-SE-05).
"""

from __future__ import annotations

import base64
import os
import re
import secrets
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import Settings

_MAGIC = b"LLE1"
_NONCE = 12
_WRAPPED = 32 + 16  # data key plus its GCM tag
_KEY = re.compile(r"^[0-9a-f]{64}$")


class StoreError(Exception):
    pass


class Backend(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


def new_key() -> str:
    """A storage name: random, so it says nothing about the client or the file."""
    return secrets.token_hex(32)


def _check(key: str) -> str:
    if not _KEY.match(key):
        raise StoreError("bad storage key")
    return key


class LocalBackend:
    """Files on a local (or mounted) disk, readable only by the service account."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        key = _check(key)
        return self.root / key[:2] / key

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            # link() fails if the name exists: a stored file is never replaced.
            os.link(tmp, path)
        except FileExistsError:
            raise StoreError("a file with this storage key already exists") from None
        finally:
            tmp.unlink(missing_ok=True)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise StoreError("no such file") from None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class EncryptedStore:
    def __init__(self, backend: Backend, master_key: bytes) -> None:
        if len(master_key) != 32:
            raise ValueError("the master key must be 32 bytes")
        self._backend = backend
        self._master = AESGCM(master_key)

    def put(self, key: str, data: bytes) -> None:
        aad = _check(key).encode()
        data_key = AESGCM.generate_key(bit_length=256)
        n1, n2 = os.urandom(_NONCE), os.urandom(_NONCE)
        wrapped = self._master.encrypt(n1, data_key, aad)
        sealed = AESGCM(data_key).encrypt(n2, data, aad)
        self._backend.put(key, _MAGIC + n1 + wrapped + n2 + sealed)

    def get(self, key: str) -> bytes:
        aad = _check(key).encode()
        blob = self._backend.get(key)
        head = len(_MAGIC) + _NONCE + _WRAPPED + _NONCE
        if len(blob) < head + 16 or not blob.startswith(_MAGIC):
            raise StoreError("not a stored file")
        at = len(_MAGIC)
        n1, at = blob[at : at + _NONCE], at + _NONCE
        wrapped, at = blob[at : at + _WRAPPED], at + _WRAPPED
        n2, at = blob[at : at + _NONCE], at + _NONCE
        try:
            data_key = self._master.decrypt(n1, wrapped, aad)
            return AESGCM(data_key).decrypt(n2, blob[at:], aad)
        except InvalidTag:
            raise StoreError("the file was altered, or the key is wrong") from None

    def delete(self, key: str) -> None:
        self._backend.delete(_check(key))


def master_key(settings: Settings) -> bytes:
    if settings.storage_key is not None:
        return base64.b64decode(settings.storage_key.get_secret_value())
    # Development and tests only (production refuses to start without storage_key).
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"ledgerline/dev-storage/v1"
    ).derive(settings.secret_key.get_secret_value().encode())


def store_from_settings(settings: Settings) -> EncryptedStore:
    return EncryptedStore(LocalBackend(settings.storage_dir), master_key(settings))
