"""Virus scanning (BRD FR-AL-04, INT-03): every file is scanned before anyone can open it.

Talks to ClamAV's daemon (clamd) with its INSTREAM command, over a Unix socket or TCP: the
bytes are streamed in chunks, so nothing is written to disk for the scan. A scanner that cannot
be reached raises `ScannerUnavailable`, and the file waits ("held until scanned", INT-03); it is
never treated as clean.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import Protocol

CHUNK = 64 * 1024


@dataclass(frozen=True)
class Verdict:
    clean: bool
    signature: str | None = None  # what was found, e.g. "Eicar-Test-Signature"


class ScannerUnavailable(Exception):
    pass


class Scanner(Protocol):
    def scan(self, data: bytes) -> Verdict: ...


class ClamdScanner:
    def __init__(self, address: str, timeout: float = 30.0) -> None:
        self.address = address
        self.timeout = timeout

    def _connect(self) -> socket.socket:
        if self.address.startswith("unix:"):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            target: str | tuple[str, int] = self.address[len("unix:") :]
        else:
            host, _, port = self.address[len("tcp:") :].rpartition(":")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target = (host, int(port))
        sock.settimeout(self.timeout)
        sock.connect(target)
        return sock

    def scan(self, data: bytes) -> Verdict:
        try:
            with self._connect() as sock:
                sock.sendall(b"zINSTREAM\0")
                for at in range(0, len(data), CHUNK):
                    chunk = data[at : at + CHUNK]
                    sock.sendall(struct.pack("!L", len(chunk)) + chunk)
                sock.sendall(struct.pack("!L", 0))
                reply = b""
                while not reply.endswith(b"\0"):
                    part = sock.recv(4096)
                    if not part:
                        break
                    reply += part
                    if len(reply) > 4096:
                        break
        except (OSError, ValueError) as exc:
            raise ScannerUnavailable(f"virus scanner not reachable ({type(exc).__name__})") from exc
        return parse_reply(reply.rstrip(b"\0").decode("utf-8", "replace"))


def parse_reply(text: str) -> Verdict:
    """'stream: OK', 'stream: <name> FOUND', or an error (never read as clean)."""
    body = text.partition(": ")[2] if ": " in text else text
    if body == "OK":
        return Verdict(clean=True)
    if body.endswith(" FOUND"):
        return Verdict(clean=False, signature=body[: -len(" FOUND")][:150])
    raise ScannerUnavailable(f"virus scanner error: {body[:150] or 'no reply'}")
