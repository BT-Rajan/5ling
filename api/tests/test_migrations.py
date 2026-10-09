from __future__ import annotations

from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from app.db import Base, make_engine
from app.models import AppMeta
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.requires_db


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = url
    return cfg


def test_upgrade_creates_baseline_with_utf8mb4_innodb(clean_db: str) -> None:
    command.upgrade(_cfg(clean_db), "head")
    engine = create_engine(clean_db)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT ENGINE, TABLE_COLLATION FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'app_meta'"
            )
        ).one()
        assert row[0] == "InnoDB"
        assert row[1].startswith("utf8mb4")
        assert (
            conn.execute(text("SELECT value FROM app_meta WHERE `key`='baseline'")).scalar()
            == "0001"
        )


def test_downgrade_then_upgrade_is_clean(clean_db: str) -> None:
    cfg = _cfg(clean_db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def test_session_is_utc_strict_and_datetimes_roundtrip_aware(clean_db: str) -> None:
    command.upgrade(_cfg(clean_db), "head")
    engine = make_engine(clean_db)
    moment = datetime(2026, 10, 31, 20, 0, 0, 123456, tzinfo=UTC)
    with Session(engine) as s:
        assert s.execute(text("SELECT @@session.time_zone")).scalar() == "+00:00"
        assert "STRICT_ALL_TABLES" in s.execute(text("SELECT @@session.sql_mode")).scalar_one()
        s.add(AppMeta(key="probe", value="x", updated_at=moment))
        s.commit()
        got = s.get(AppMeta, "probe")
        assert got is not None
        assert got.updated_at == moment
        assert got.updated_at.tzinfo is not None
    engine.dispose()


def test_strict_mode_rejects_oversized_value(clean_db: str) -> None:
    command.upgrade(_cfg(clean_db), "head")
    engine = make_engine(clean_db)
    with Session(engine) as s:
        s.add(AppMeta(key="k" * 65, value="x", updated_at=datetime.now(UTC)))
        with pytest.raises(Exception, match=r"Data too long"):
            s.commit()
    engine.dispose()


def test_models_and_migrations_agree(clean_db: str) -> None:
    """If someone edits a model without writing a migration (or the reverse), this fails."""
    command.upgrade(_cfg(clean_db), "head")
    engine = create_engine(clean_db)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []
