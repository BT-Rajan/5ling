from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.config import Settings
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

TEST_DB_URL = os.environ.get("LL_TEST_DATABASE_URL")
# Nothing listens here, so tests that do not need a database never touch a real one.
UNREACHABLE_DB = "mysql+pymysql://nobody:nothing@127.0.0.1:1/none"
GOOD_SECRET = "9f2c6a1be07d4835a6c1f0e29b7d3c58e41a0f6b"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "env": "test",
        "database_url": UNREACHABLE_DB,
        "secret_key": GOOD_SECRET,
        "allowed_hosts": "testserver,localhost",
        "allowed_origins": "https://app.example.test",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    return make_settings


@pytest.fixture
def app() -> FastAPI:
    return create_app(make_settings())


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def db_url() -> str:
    if not TEST_DB_URL:
        pytest.skip("LL_TEST_DATABASE_URL not set")
    return TEST_DB_URL


@pytest.fixture
def clean_db(db_url: str) -> Iterator[str]:
    """Empty the test database (drop every table) before and after."""
    # This fixture drops every table. Refuse to run against anything not clearly a test database.
    database = make_url(db_url).database or ""
    if not database.endswith("_test"):
        pytest.fail(f"refusing to wipe '{database}': test database names must end in _test")
    engine = create_engine(db_url)

    def wipe() -> None:
        with engine.begin() as conn:
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
            tables = [r[0] for r in conn.execute(text("SHOW TABLES"))]
            for table in tables:
                conn.execute(text(f"DROP TABLE IF EXISTS `{table}`"))
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

    wipe()
    yield db_url
    wipe()
    engine.dispose()


def _wipe(engine) -> None:  # type: ignore[no-untyped-def]
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for (table,) in list(conn.execute(text("SHOW TABLES"))):
            conn.execute(text(f"DROP TABLE IF EXISTS `{table}`"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))


@pytest.fixture
def migrated_db(db_url: str) -> str:
    """A test database at the latest migration with empty data tables (fast: no re-migrating)."""
    database = make_url(db_url).database or ""
    if not database.endswith("_test"):
        pytest.fail(f"refusing to use '{database}': test database names must end in _test")
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = db_url
    head = ScriptDirectory.from_config(cfg).get_current_head()
    engine = create_engine(db_url)
    with engine.connect() as conn:
        tables = {r[0] for r in conn.execute(text("SHOW TABLES"))}
        current = (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            if "alembic_version" in tables
            else None
        )
    if current != head:
        _wipe(engine)
        command.upgrade(cfg, "head")
    else:
        with engine.begin() as conn:
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
            for table in tables - {"alembic_version", "app_meta"}:
                conn.execute(text(f"TRUNCATE TABLE `{table}`"))
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    engine.dispose()
    return db_url


@pytest.fixture
def web(migrated_db: str) -> Iterator[TestClient]:
    """The real app on the migrated test database."""
    with TestClient(create_app(make_settings(database_url=migrated_db))) as client:
        yield client
