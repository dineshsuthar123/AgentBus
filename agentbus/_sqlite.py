from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable
from time import sleep as _sleep

from agentbus._failure_injection import (
    FailureInjectionPoint,
    FailureProbe,
    failure_due,
)


DEFAULT_TRANSACTION_RETRY_DELAYS = (0.01, 0.05, 0.1)
_MAX_TRANSACTION_RETRIES = 8
_MAX_RETRY_DELAY_SECONDS = 5.0


def normalize_transaction_retry_delays(delays: Iterable[float]) -> tuple[float, ...]:
    try:
        normalized = tuple(float(delay) for delay in delays)
    except (TypeError, ValueError) as exc:
        raise ValueError("transaction retry delays must be numeric") from exc
    if len(normalized) > _MAX_TRANSACTION_RETRIES:
        raise ValueError(
            f"transaction retry delays support at most {_MAX_TRANSACTION_RETRIES} retries"
        )
    if any(
        not math.isfinite(delay)
        or delay < 0
        or delay > _MAX_RETRY_DELAY_SECONDS
        for delay in normalized
    ):
        raise ValueError(
            "transaction retry delays must be finite values between 0 and 5 seconds"
        )
    return normalized


def begin_immediate_with_retry(
    connection: sqlite3.Connection,
    *,
    retry_delays: tuple[float, ...] = DEFAULT_TRANSACTION_RETRY_DELAYS,
    failure_probe: FailureProbe | None = None,
    failure_scope: str | None = None,
) -> None:
    """Acquire a SQLite writer transaction without replaying its write body."""

    for attempt in range(len(retry_delays) + 1):
        try:
            if failure_due(
                failure_probe,
                FailureInjectionPoint.SQLITE_BUSY,
                scope=failure_scope,
            ):
                raise sqlite3.OperationalError(
                    "database is busy (controlled failure injection)"
                )
            connection.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as exc:
            if not is_sqlite_busy_error(exc) or attempt == len(retry_delays):
                raise
            _sleep(retry_delays[attempt])


def is_sqlite_busy_error(error: sqlite3.Error) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int) and (code & 0xFF) in {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    }:
        return True
    message = str(error).casefold()
    return "locked" in message or "busy" in message
