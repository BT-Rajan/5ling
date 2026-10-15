"""Counting server errors per minute for the monitor's error-spike alert (BRD NFR-OB-01)."""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import Engine, text

from app.timeutil import utcnow

log = logging.getLogger("ledgerline.ops")


def error_counter(engine: Engine) -> Callable[[], None]:
    """A function that adds one to this minute's server-error count. It never raises: counting
    must not turn one failure into two."""

    def record() -> None:
        minute = utcnow().replace(second=0, microsecond=0)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO error_minutes (minute, count) VALUES (:m, 1)"
                        " ON DUPLICATE KEY UPDATE count = count + 1"
                    ),
                    {"m": minute.replace(tzinfo=None)},
                )
        except Exception:  # the database may be the very thing failing
            log.warning("could not count a server error", exc_info=True)

    return record
