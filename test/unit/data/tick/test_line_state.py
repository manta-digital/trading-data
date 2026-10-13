"""``line_state.classify``: one failure → line mapping for the CLI and the API (231)."""

from __future__ import annotations

import psycopg
import pytest

from manta_trading.data.tick.line_state import (
    TickLineState,
    TickUnavailable,
    classify,
)
from manta_trading.data.tick.store_context import TickPreflightError
from manta_trading.data.tick.tick_calendar import TickCalendarError

SECRET = "s3cret-pw"


def test_an_operational_error_is_unreachable() -> None:
    line = classify(psycopg.OperationalError("connection refused"), None, "20s")

    assert line == TickUnavailable(TickLineState.UNREACHABLE, "connection refused")


def test_a_calendar_error_is_unreachable() -> None:
    line = classify(TickCalendarError("calendar down"), None, "20s")

    assert line.state is TickLineState.UNREACHABLE


@pytest.mark.parametrize("timeout", ["10 s", "20s"])
def test_a_cancelled_statement_names_the_timeout_it_was_given(timeout: str) -> None:
    error = psycopg.errors.QueryCanceled("canceling statement")

    line = classify(error, None, timeout)

    assert line == TickUnavailable(
        TickLineState.UNREACHABLE,
        f"statement timeout ({timeout}): canceling statement",
    )


@pytest.mark.parametrize(
    "error",
    [TickPreflightError("range outside calendar"), psycopg.DataError("bad value")],
)
def test_a_refusal_or_other_database_error_is_failed(error: Exception) -> None:
    assert classify(error, None, "20s").state is TickLineState.FAILED


def test_the_password_is_scrubbed_and_only_the_first_line_kept() -> None:
    error = psycopg.OperationalError(f"auth failed for {SECRET}\nsecond line")

    line = classify(error, SECRET, "20s")

    assert line.detail == "auth failed for ***"


def test_no_password_leaves_the_text_alone() -> None:
    error = psycopg.OperationalError(f"auth failed for {SECRET}")

    assert classify(error, None, "20s").detail == f"auth failed for {SECRET}"
