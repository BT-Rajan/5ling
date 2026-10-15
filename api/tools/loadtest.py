"""Load test (BRD NFR-PF-01, NFR-SC-01): screens under 2 seconds and searches under 1 second with
5,000 clients and 50,000 cycles.

    cd api
    LL_DATABASE_URL=mysql+pymysql://owner:pw@127.0.0.1:3306/ledgerline_load LL_SECRET_KEY=... \\
        .venv/bin/alembic upgrade head
    LL_DATABASE_URL=... LL_SECRET_KEY=... .venv/bin/python tools/loadtest.py [--clients 5000]

It refuses a database whose name does not end in "_load". The first run seeds it through the real
API (one published package of two monthly filings, 20 owners, and the clients; the cycle generator
makes their cycles, about 19 a client per filing), which takes a while; later runs reuse it. Then
it times each screen and search 20 times, in process (server time, without the network), and
prints the 95th percentile against the target. Exit 1 if any misses.
"""

from __future__ import annotations

import argparse
import base64
import secrets
import statistics
import string
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import mfa
from app.auth.passwords import hash_password
from app.auth.sessions import cookie_name, create_session
from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.main import create_app
from app.models import Client, Cycle, Role, Ticket, User
from app.timeutil import utcnow
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

SCREEN_SECONDS = 2.0
SEARCH_SECONDS = 1.0
RUNS = 20


def pan_for(n: int) -> str:
    letters = string.ascii_uppercase
    a, b, c = n // (26 * 26) % 26, n // 26 % 26, n % 26
    return f"{letters[a]}{letters[b]}{letters[c]}P{letters[n % 26]}{n % 10000:04d}{letters[(n // 7) % 26]}"


def signed_in(app: Any, factory: Any, settings: Any, email: str, role: Role) -> TestClient:
    with factory() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email,
                full_name=email.split("@")[0].title(),
                role=role,
                password_hash=hash_password("load-test-only-password"),
                is_active=True,
                # Enrolled in two-step sign-in, as every Staff member and Administrator must be.
                totp_secret_enc=mfa.encrypt_secret(
                    base64.b32encode(secrets.token_bytes(20)).decode(),
                    settings.secret_key.get_secret_value(),
                ),
                totp_enabled_at=utcnow(),
            )
            db.add(user)
            db.flush()
        token, row = create_session(db, user, "127.0.0.1")
        db.commit()
        csrf = row.csrf_token
    client = TestClient(app)
    client.cookies.set(cookie_name(settings), token)
    client.headers["X-CSRF-Token"] = csrf
    return client


def ok(resp: Any, status: int = 200) -> Any:
    if resp.status_code != status:
        raise SystemExit(
            f"{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


def seed(admin: TestClient, factory: Any, settings: Any, app: Any, clients: int) -> None:
    with factory() as db:
        have = int(db.scalar(select(func.count(Client.id))) or 0)
    if have >= clients:
        return
    print(f"Seeding {clients - have} clients (once)...", flush=True)
    owners = []
    for i in range(20):
        signed_in(app, factory, settings, f"owner{i}@load.example.in", Role.STAFF)
    with factory() as db:
        owners = list(db.scalars(select(User.id).where(User.role == Role.STAFF)))
    packages = ok(admin.get("/api/clients/options"))["packages"]
    if not packages:
        filings = []
        for code in ("GSTR-1", "GSTR-3B"):
            f = ok(admin.post("/api/admin/filings", json={"code": code, "name": code}), 201)
            cal = f"/api/admin/filings/{f['id']}/calendar"
            ok(
                admin.put(
                    f"{cal}/draft",
                    json={"frequency": "monthly", "due": {"months_after": 1, "day": 11}},
                )
            )
            ok(admin.post(f"{cal}/publish"))
            filings.append({"filing_id": f["id"]})
        pkg = ok(admin.post("/api/admin/packages", json={"name": "GST Monthly"}), 201)
        ok(
            admin.put(
                f"/api/admin/packages/{pkg['id']}/draft",
                json={"fee": "1000", "billing_frequency": "monthly", "filings": filings},
            )
        )
        ok(admin.post(f"/api/admin/packages/{pkg['id']}/publish"))
        packages = ok(admin.get("/api/clients/options"))["packages"]
    package_id = packages[0]["id"]
    started = time.perf_counter()
    for n in range(have, clients):
        ok(
            admin.post(
                "/api/clients",
                json={
                    "kind": "individual",
                    "legal_name": f"Load Client {n:05d}",
                    "pan": pan_for(n),
                    "package_id": package_id,
                    "owner_id": owners[n % len(owners)],
                    "contacts": [
                        {"name": "Contact", "email": f"c{n}@load.example.in", "is_primary": True}
                    ],
                },
            ),
            201,
        )
        if n % 250 == 0:
            print(f"  {n} clients, {time.perf_counter() - started:.0f}s", flush=True)


def timed(client: TestClient, path: str) -> list[float]:
    out = []
    for _ in range(RUNS):
        start = time.perf_counter()
        resp = client.get(path)
        out.append(time.perf_counter() - start)
        if resp.status_code != 200:
            raise SystemExit(f"GET {path} -> {resp.status_code}: {resp.text[:300]}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clients", type=int, default=5000)
    args = parser.parse_args()
    settings = get_settings()
    url = settings.database_url.get_secret_value()
    if not (make_url(url).database or "").endswith("_load"):
        raise SystemExit("Use a scratch database whose name ends in _load.")
    engine = make_engine(url)
    factory = make_session_factory(engine)
    app = create_app(settings, engine)
    admin = signed_in(app, factory, settings, "boss@load.example.in", Role.ADMINISTRATOR)
    seed(admin, factory, settings, app, args.clients)
    staff = signed_in(app, factory, settings, "owner0@load.example.in", Role.STAFF)

    with factory() as db:
        n_clients = int(db.scalar(select(func.count(Client.id))) or 0)
        n_cycles = int(db.scalar(select(func.count(Cycle.id))) or 0)
        some = db.scalar(select(Client.client_id).order_by(Client.id.desc()).limit(1))
        number = db.scalar(select(Ticket.number).order_by(Ticket.id.desc()).limit(1))
    code = f"TKT-{number:06d}"
    print(f"{n_clients} clients, {n_cycles} cycles. {RUNS} runs each; 95th percentile.\n")
    cases = [
        ("search", admin, "/api/clients?q=Load%20Client%2004", SEARCH_SECONDS),
        ("search", admin, f"/api/clients?q={pan_for(1234)}", SEARCH_SECONDS),
        ("search", admin, "/api/tickets?q=GSTR&limit=50", SEARCH_SECONDS),
        ("screen", admin, "/api/clients?limit=50", SCREEN_SECONDS),
        ("screen", admin, f"/api/clients/{some}", SCREEN_SECONDS),
        ("screen", admin, f"/api/tickets/{code}", SCREEN_SECONDS),
        ("screen", admin, "/api/worklist", SCREEN_SECONDS),
        ("screen", staff, "/api/worklist", SCREEN_SECONDS),
        ("screen", admin, "/api/dashboard", SCREEN_SECONDS),
        ("screen", staff, "/api/dashboard", SCREEN_SECONDS),
        ("screen", admin, "/api/dashboard/list?figure=overdue_filings", SCREEN_SECONDS),
        ("screen", admin, "/api/reports/filing-status", SCREEN_SECONDS),
        ("screen", admin, "/api/reports/fee-aging", SCREEN_SECONDS),
    ]
    failed = 0
    for kind, client, path, limit in cases:
        times = sorted(timed(client, path))
        p95 = times[int(len(times) * 0.95) - 1]
        verdict = "PASS" if p95 <= limit else "FAIL"
        failed += verdict == "FAIL"
        who = "admin" if client is admin else "staff"
        print(
            f"{verdict}  {kind:6} {who:5} p95 {p95:6.3f}s median {statistics.median(times):6.3f}s  {path}"
        )
    print(
        f"\n{'All within target.' if not failed else f'{failed} over target.'} Run at {utcnow():%Y-%m-%d %H:%M} UTC."
    )
    engine.dispose()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
