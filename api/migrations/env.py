from __future__ import annotations

import app.models  # noqa: F401  (register models on Base.metadata)
from alembic import context
from app.config import get_settings
from app.db import Base, make_engine

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    # Tests and tools may pass a URL through the alembic config; otherwise use app settings.
    return config.attributes.get("url") or get_settings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
