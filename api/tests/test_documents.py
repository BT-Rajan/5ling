"""Chunk C16: documents and storage (BRD FR-AL-04, FR-AL-05, INT-03, INT-04, NFR-SE-04/05)."""

from __future__ import annotations

import io
import socketserver
import struct
import threading
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from app.db import make_engine, make_session_factory
from app.files.documents import make_link, read_link, scan_pending
from app.files.scan import ClamdScanner, ScannerUnavailable, Verdict, parse_reply
from app.files.store import EncryptedStore, LocalBackend, StoreError, new_key
from app.files.types import FileRefused, check, clean_name
from app.models import DocumentVersion, Role, ScanStatus
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from tests.audit_helpers import entries, events
from tests.conftest import make_settings
from tests.helpers import Signed, db_session, make_user, sign_in_directly
from tests.test_clients import _ok, admin, client_body, create, publish_package
from tests.test_tickets import first_ticket

__all__ = ["admin"]  # the fixture, re-used from test_clients

PDF = b"%PDF-1.7\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def office(kind: str, *, macros: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml" if kind == "xlsx" else "word/document.xml", "<x/>")
        if macros:
            z.writestr("xl/vbaProject.bin", b"\0")
    return buf.getvalue()


# --- Storage: encrypted, never overwritten ------------------------------------------------------


def test_files_are_encrypted_bound_to_their_name_and_never_overwritten(tmp_path: Path) -> None:
    backend = LocalBackend(tmp_path)
    store = EncryptedStore(backend, b"k" * 32)
    key, other = new_key(), new_key()
    store.put(key, PDF)
    raw = backend.get(key)
    assert PDF not in raw  # only ciphertext on disk
    assert store.get(key) == PDF
    with pytest.raises(StoreError, match="already exists"):
        store.put(key, b"%PDF-replacement")
    assert store.get(key) == PDF
    # A stored file copied under another name, or read with another key, does not open.
    backend.put(other, raw)
    with pytest.raises(StoreError, match="altered"):
        store.get(other)
    with pytest.raises(StoreError, match="altered"):
        EncryptedStore(backend, b"x" * 32).get(key)
    for bad in ("../etc/passwd", "a" * 63, "Z" * 64):
        with pytest.raises(StoreError):
            store.get(bad)
    assert (tmp_path / key[:2] / key).stat().st_mode & 0o777 == 0o600


# --- Types: by content, not by name ---------------------------------------------------------


def test_files_are_checked_by_content_and_names_are_cleaned() -> None:
    assert check("return.pdf", PDF, 1000)[1].content_type == "application/pdf"
    assert check("Sales.xlsx", office("xlsx"), 10_000)[0] == "Sales.xlsx"
    assert check("notes.csv", b"date,amount\n2026-04-01,100\n", 1000)[1].extension == "csv"
    for name, data, why in (
        ("invoice.pdf", b"MZ\x90\x00 an executable", "not really a PDF"),
        ("setup.exe", b"MZ\x90\x00", "not accepted"),
        ("book.xlsm", office("xlsx"), "macros"),
        ("book.xlsx", office("xlsx", macros=True), "macros"),
        ("data.csv", b"a,b\0c", "not really a CSV"),
        ("empty.pdf", b"", "empty"),
        ("big.pdf", PDF + b"x" * 2000, "larger than"),
    ):
        with pytest.raises(FileRefused, match=why):
            check(name, data, 1500)
    assert clean_name("../../etc/passwd.pdf") == "passwd.pdf"
    assert clean_name("C:\\Users\\a\\Bank\u202estatement.pdf") == "Bankstatement.pdf"
    assert clean_name("  report  2026 .pdf ") == "report 2026 .pdf"
    with pytest.raises(FileRefused):
        clean_name(" ... ")


# --- The ClamAV protocol, against a tiny local clamd ------------------------------------------


@dataclass
class FakeClamd:
    received: list[bytes] = field(default_factory=list)


def _clamd_handler(state: FakeClamd) -> type[socketserver.BaseRequestHandler]:
    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            sock = self.request
            command = b""
            while not command.endswith(b"\0"):
                command += sock.recv(1)
            assert command == b"zINSTREAM\0"
            data = b""
            while True:
                size = struct.unpack("!L", self._exactly(4))[0]
                if size == 0:
                    break
                data += self._exactly(size)
            state.received.append(data)
            found = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in data
            sock.sendall(b"stream: Eicar-Test-Signature FOUND\0" if found else b"stream: OK\0")

        def _exactly(self, n: int) -> bytes:
            out = b""
            while len(out) < n:
                out += self.request.recv(n - len(out))
            return out

    return Handler


@pytest.fixture
def clamd() -> Iterator[tuple[FakeClamd, str]]:
    state = FakeClamd()
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _clamd_handler(state))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield state, f"tcp:127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_clamd_streams_the_file_and_reads_the_verdict(clamd: tuple[FakeClamd, str]) -> None:
    state, address = clamd
    big = PDF + b"x" * 200_000  # several chunks
    assert ClamdScanner(address).scan(big) == Verdict(clean=True)
    assert state.received[0] == big
    assert ClamdScanner(address).scan(EICAR) == Verdict(False, "Eicar-Test-Signature")
    with pytest.raises(ScannerUnavailable):
        ClamdScanner("tcp:127.0.0.1:1").scan(PDF)
    with pytest.raises(ScannerUnavailable):
        parse_reply("INSTREAM size limit exceeded. ERROR")
    with pytest.raises(ScannerUnavailable):
        parse_reply("")


def test_download_links_are_signed_short_lived_and_personal() -> None:
    settings = make_settings()
    token = make_link(settings, 42, 7, now=1_000_000)
    assert read_link(settings, token, now=1_000_100) == (42, 7)
    assert read_link(settings, token, now=1_000_301) is None  # five minutes
    forged = token.replace(".7.", ".8.", 1)
    assert read_link(settings, forged, now=1_000_100) is None
    assert (
        read_link(
            make_settings(secret_key="f" * 8 + "0123456789abcdefghijklmnopqrstuvwxyz"),
            token,
            now=1_000_100,
        )
        is None
    )
    assert read_link(settings, "1.1.1.bad") is None


# --- With a database ---------------------------------------------------------------------------


class FakeScanner:
    def __init__(self) -> None:
        self.down = False

    def scan(self, data: bytes) -> Verdict:
        if self.down:
            raise ScannerUnavailable("down")
        if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in data:
            return Verdict(clean=False, signature="Eicar-Test-Signature")
        return Verdict(clean=True)


@pytest.fixture
def scanner(web: TestClient) -> FakeScanner:
    fake = FakeScanner()
    web.app.state.scanner = fake  # type: ignore[attr-defined]
    return fake


def setup_ticket(web: TestClient, admin: Signed, url: str) -> str:
    pid = publish_package(web, admin)
    cid = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)["client_id"]
    return first_ticket(url, cid)


def upload(
    web: TestClient, signed: Signed, code: str, name: str, data: bytes, **params: Any
) -> Any:
    return web.post(
        f"/api/uploads/tickets/{code}",
        params=params,
        content=data,
        headers={
            **signed.headers,
            "X-File-Name": quote(name),
            "Content-Type": "application/octet-stream",
        },
    )


def download(web: TestClient, signed: Signed, version_id: int) -> Any:
    link = _ok(web.post(f"/api/documents/versions/{version_id}/link", headers=signed.headers))
    assert link["url"].startswith("/api/files/")
    return web.get(link["url"])


# --- Done when: a new upload becomes version 2; an infected file is rejected and logged; no public
# file URL exists -------------------------------------------------------------------------------


@pytest.mark.requires_db
def test_a_new_upload_becomes_version_2_and_both_stay_viewable(
    web: TestClient, admin: Signed, migrated_db: str, scanner: FakeScanner
) -> None:
    code = setup_ticket(web, admin, migrated_db)
    first = _ok(
        upload(web, admin, code, "Sales register April.pdf", PDF, title="Sales register"), 201
    )
    assert [(d["title"], d["latest_version"]) for d in first] == [("Sales register", 1)]
    doc = first[0]["id"]
    v2_bytes = PDF + b"% corrected\n"
    second = _ok(
        upload(web, admin, code, "Sales register (corrected).pdf", v2_bytes, document_id=doc), 201
    )
    versions = second[0]["versions"]
    assert [(v["version"], v["filename"], v["scan_status"]) for v in versions] == [
        (2, "Sales register (corrected).pdf", "clean"),
        (1, "Sales register April.pdf", "clean"),
    ]
    assert versions[0]["uploaded_by"] == "Test Person"
    # Both versions download, each exactly as uploaded.
    newest, oldest = (
        download(web, admin, versions[0]["id"]),
        download(web, admin, versions[1]["id"]),
    )
    assert (newest.content, oldest.content) == (v2_bytes, PDF)
    assert newest.headers["content-type"] == "application/pdf"
    assert newest.headers["cache-control"] == "no-store"
    assert "attachment;" in newest.headers["content-disposition"]
    assert newest.headers["x-content-type-options"] == "nosniff"
    trail = events(migrated_db)
    assert trail.count("document.uploaded") == 2
    assert trail.count("document.viewed") == 2
    assert trail.count("document.downloaded") == 2
    # Another ticket's document cannot be versioned from here.
    assert (
        upload(web, admin, code, "x.pdf", PDF, document_id=999999).json()["error"]["code"]
        == "file_refused"
    )


@pytest.mark.requires_db
def test_an_infected_file_is_rejected_logged_and_never_stored(
    web: TestClient, admin: Signed, migrated_db: str, scanner: FakeScanner
) -> None:
    code = setup_ticket(web, admin, migrated_db)
    refused = upload(web, admin, code, "invoice.txt", EICAR)
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "infected"
    found = [e for e in entries(migrated_db) if e.event == "document.infected"]
    assert len(found) == 1
    assert found[0].details["reason"] == "Eicar-Test-Signature"
    with db_session(migrated_db) as db:
        assert db.scalar(select(DocumentVersion.id)) is None
    assert _ok(web.get(f"/api/tickets/{code}/documents")) == []

    bad = upload(web, admin, code, "payload.pdf", b"MZ\x90\x00")
    assert bad.status_code == 422
    assert bad.json()["error"]["message"] == "This file is not really a PDF file."
    assert "document.rejected" in events(migrated_db)


@pytest.mark.requires_db
def test_no_file_is_reachable_without_a_fresh_personal_link(
    web: TestClient, admin: Signed, migrated_db: str, scanner: FakeScanner
) -> None:
    code = setup_ticket(web, admin, migrated_db)
    doc = _ok(upload(web, admin, code, "a.pdf", PDF), 201)[0]
    version = doc["versions"][0]["id"]
    link = _ok(web.post(f"/api/documents/versions/{version}/link", headers=admin.headers))["url"]
    stranger = TestClient(web.app)
    assert stranger.get(link).status_code == 401  # no session: nothing
    other_id = make_user(migrated_db, "other@firm.in", Role.STAFF)
    other = TestClient(web.app)
    signed = sign_in_directly(migrated_db, other, other_id)
    assert other.get(link).status_code == 410  # someone else's link
    assert (
        other.post(f"/api/documents/versions/{version}/link", headers=signed.headers).status_code
        == 403
    )
    assert other.get(f"/api/tickets/{code}/documents").status_code == 403
    assert upload(other, signed, code, "x.pdf", PDF).status_code == 403
    assert web.get("/api/files/1.1.1.forged").status_code == 410
    # The stored file has a random name and holds only ciphertext.
    with db_session(migrated_db) as db:
        stored = db.scalar(select(DocumentVersion))
        assert stored is not None
        key = stored.storage_key
    assert len(key) == 64
    raw = (Path(web.app.state.settings.storage_dir) / key[:2] / key).read_bytes()  # type: ignore[attr-defined]
    assert PDF not in raw


@pytest.mark.requires_db
def test_a_file_is_held_while_the_scanner_is_down_then_scanned(
    web: TestClient, admin: Signed, migrated_db: str, scanner: FakeScanner
) -> None:
    code = setup_ticket(web, admin, migrated_db)
    scanner.down = True
    held = _ok(upload(web, admin, code, "a.pdf", PDF, title="Held"), 201)[0]["versions"][0]
    bad = _ok(upload(web, admin, code, "b.txt", EICAR, title="Bad"), 201)[-1]["versions"][0]
    assert (held["scan_status"], held["scan_label"]) == ("pending", "Waiting for the virus check")
    refused = web.post(f"/api/documents/versions/{held['id']}/link", headers=admin.headers)
    assert refused.status_code == 409  # held: not shown to anyone

    engine = make_engine(migrated_db)
    try:
        factory = make_session_factory(engine)
        store = web.app.state.store  # type: ignore[attr-defined]
        assert scan_pending(factory, store, scanner).waiting == 1
        scanner.down = False
        tally = scan_pending(factory, store, scanner)
        assert (tally.clean, tally.infected) == (1, 1)
    finally:
        engine.dispose()
    assert download(web, admin, held["id"]).content == PDF
    docs = _ok(web.get(f"/api/tickets/{code}/documents"))
    assert docs[1]["versions"][0]["scan_status"] == "infected"
    assert (
        web.post(f"/api/documents/versions/{bad['id']}/link", headers=admin.headers).status_code
        == 409
    )
    with db_session(migrated_db) as db:
        version = db.get(DocumentVersion, bad["id"])
        assert version is not None
        assert version.scan_status is ScanStatus.INFECTED
        with pytest.raises(StoreError):
            store.get(version.storage_key)  # the file itself is gone
    assert "document.infected" in events(migrated_db)


@pytest.mark.requires_db
def test_versions_cannot_be_rewritten_or_deleted(
    web: TestClient, admin: Signed, migrated_db: str, scanner: FakeScanner
) -> None:
    code = setup_ticket(web, admin, migrated_db)
    _ok(upload(web, admin, code, "a.pdf", PDF), 201)
    engine = create_engine(migrated_db)
    try:
        for sql, message in (
            ("UPDATE document_versions SET storage_key = REPEAT('a', 64)", "cannot be changed"),
            ("UPDATE document_versions SET filename = 'other.pdf'", "cannot be changed"),
            ("UPDATE document_versions SET scan_status = 'pending'", "cannot be changed"),
            ("DELETE FROM document_versions", "never deleted"),
            ("UPDATE documents SET latest_version = 0", "cannot be changed"),
            ("DELETE FROM documents", "never deleted"),
        ):
            with pytest.raises(Exception, match=message), engine.begin() as conn:
                conn.execute(text(sql))
    finally:
        engine.dispose()


@pytest.mark.requires_db
def test_uploads_have_their_own_size_limit(
    web: TestClient, admin: Signed, migrated_db: str, scanner: FakeScanner
) -> None:
    code = setup_ticket(web, admin, migrated_db)
    limit = web.app.state.settings.max_upload_bytes  # type: ignore[attr-defined]
    over = upload(web, admin, code, "huge.pdf", PDF + b"x" * limit)
    assert over.status_code == 422  # just over: refused with a reason
    assert "larger than" in over.json()["error"]["message"]
    far_over = upload(web, admin, code, "huge.pdf", PDF + b"x" * (limit + 4096))
    assert far_over.status_code == 413  # well over: cut off before it is read in full
    # Above the normal request limit, but within the upload limit: accepted.
    big = PDF + b"x" * (2 * 1024 * 1024)
    assert upload(web, admin, code, "big.pdf", big).status_code == 201
    assert (
        web.post(
            "/api/tickets", content=b"x" * (2 * 1024 * 1024), headers=admin.headers
        ).status_code
        == 413
    )
