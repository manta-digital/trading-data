"""Unit tests: the shared Kalshi refusals (188 code review F002).

`kalshi_catalog.py` and `kalshi_timeseries.py` each carried a near-identical
`_admit_rows`/`_not_found`. They are one implementation now, with the only
thing that legitimately differed — the remedy text — as a parameter.

These tests pin the wording, because the consolidation must not change a
single byte a client sees. Both remedies are asserted so the parameter cannot
collapse back to one message.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from manta_trading.api_server.admission import (
    NARROW_FILTER,
    NARROW_WINDOW,
    admit_rows,
    not_found,
)


class TestAdmitRows:
    def test_under_the_ceiling_admits(self) -> None:
        assert admit_rows(74_999, 75_000, remedy=NARROW_FILTER) is None

    def test_exactly_at_the_ceiling_admits(self) -> None:
        """The ceiling is inclusive: `count > max_rows` refuses, not `>=`."""
        assert admit_rows(75_000, 75_000, remedy=NARROW_WINDOW) is None

    @pytest.mark.parametrize(
        ("remedy", "expected"),
        [
            (NARROW_FILTER, "narrow the filter"),
            (NARROW_WINDOW, "narrow start/end"),
        ],
    )
    def test_over_the_ceiling_refuses_with_its_own_remedy(
        self, remedy: str, expected: str
    ) -> None:
        with pytest.raises(HTTPException) as exc:
            admit_rows(88_867, 75_000, remedy=remedy)

        assert exc.value.status_code == 422
        assert exc.value.detail == (
            f"the request matches 88,867 rows, over the 75,000 row limit; {expected}"
        )

    def test_the_message_quotes_the_live_numbers(self) -> None:
        """Neither number is a literal, so the message cannot drift from the
        ceiling actually in force — and the real count is what tells a caller
        how far to narrow."""
        with pytest.raises(HTTPException) as exc:
            admit_rows(1_234_567, 1_000, remedy=NARROW_FILTER)

        assert "1,234,567 rows" in exc.value.detail
        assert "1,000 row limit" in exc.value.detail


class TestNotFound:
    @pytest.mark.parametrize(
        ("resource", "ticker"),
        [("Market", "NOSUCHMARKET"), ("Series", "NOSUCHSERIES"), ("Event", "NOPE")],
    )
    def test_wording_is_resource_scoped(self, resource: str, ticker: str) -> None:
        exc = not_found(resource, ticker)

        assert exc.status_code == 404
        assert exc.detail == f"{resource} '{ticker}' not found"

    def test_it_is_returned_not_raised(self) -> None:
        """Callers raise it themselves, which is what lets a route build it
        inside an executor callback where a raise would be lost."""
        assert isinstance(not_found("Market", "X"), HTTPException)
