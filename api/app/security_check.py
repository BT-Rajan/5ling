"""A security review of the running installation (BRD NFR-SE-04): `python -m app.cli
security-check`, run before go-live and after any server change. Exit 1 if anything fails.

What it checks:
- documents: an encryption key is configured, kept out of the database URL, and different from
  the session secret (the app encrypts every file with it; see app/files/store.py);
- database at rest: InnoDB encrypts new tables by default, every Ledgerline table is encrypted,
  and the redo, undo and binary logs are encrypted (MySQL's keyring must be set up for these;
  docs/SECURITY_REVIEW.md has the steps);
- database in transit: the server requires TLS from every client;
- the app is in production mode, so its own refusals (example sender, missing keys) apply.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, text

from app.config import Settings


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _var(engine: Engine, name: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(text("SHOW GLOBAL VARIABLES LIKE :n"), {"n": name}).first()
    return str(row[1]) if row else None


def key_checks(settings: Settings) -> list[Check]:
    key = settings.storage_key.get_secret_value() if settings.storage_key else None
    secret = settings.secret_key.get_secret_value()
    db_url = settings.database_url.get_secret_value()
    return [
        Check("production mode", settings.env == "production", f"env is {settings.env}"),
        Check(
            "document key set",
            key is not None,
            "LL_STORAGE_KEY is set" if key else "LL_STORAGE_KEY is not set",
        ),
        Check(
            "document key kept apart",
            key is not None and key != secret and key not in db_url,
            "the document key is not the session secret and not in the database URL",
        ),
    ]


def database_checks(engine: Engine) -> list[Check]:
    with engine.connect() as conn:
        schema = conn.execute(text("SELECT DATABASE()")).scalar()
        # CREATE_OPTIONS shows ENCRYPTION="Y" for an encrypted table; it needs no PROCESS right.
        rows = conn.execute(
            text(
                "SELECT TABLE_NAME, CREATE_OPTIONS FROM information_schema.TABLES"
                " WHERE TABLE_SCHEMA = :s AND TABLE_TYPE = 'BASE TABLE'"
            ),
            {"s": schema},
        ).all()
    plain = sorted(
        name for name, options in rows if 'ENCRYPTION="Y"' not in str(options or "").upper()
    )
    out = [
        Check(
            "tables encrypted at rest",
            bool(rows) and not plain,
            f"{len(rows) - len(plain)} of {len(rows)} tables encrypted"
            + (f"; not: {', '.join(plain[:5])}{'...' if len(plain) > 5 else ''}" if plain else ""),
        )
    ]
    for var, label in (
        ("default_table_encryption", "new tables encrypted by default"),
        ("innodb_redo_log_encrypt", "redo log encrypted"),
        ("innodb_undo_log_encrypt", "undo log encrypted"),
        ("binlog_encryption", "binary log encrypted"),
        ("require_secure_transport", "TLS required for database connections"),
    ):
        value = _var(engine, var)
        out.append(Check(label, (value or "").upper() == "ON", f"{var} = {value}"))
    return out


def review(settings: Settings, engine: Engine) -> list[Check]:
    return key_checks(settings) + database_checks(engine)
