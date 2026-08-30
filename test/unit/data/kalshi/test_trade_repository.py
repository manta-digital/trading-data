"""``PageCounts`` accounting (slice 265, Task 3.2) — pure, no database.

The identity ``fetched = written + unknown + excluded + duplicates`` is
structural: it raises, never ``assert``s, and ``selected`` is carried from
SQL so the check can actually fail.
"""

from __future__ import annotations

import pytest

from manta_trading.data.kalshi.trade_repository import PageAccountingError, PageCounts


class TestPageCounts:
    def test_identity_holds_and_duplicates_derive(self):
        counts = PageCounts(
            fetched=10, unknown_market=2, excluded_by_rule=3, selected=5, written=4
        )
        assert counts.duplicates == 1
        assert (
            counts.fetched
            == counts.written
            + counts.unknown_market
            + counts.excluded_by_rule
            + counts.duplicates
        )

    def test_rows_lost_before_classification_raise(self):
        """A page of 10 of which only 9 reached ``classified``."""
        with pytest.raises(PageAccountingError, match="fetched 10"):
            PageCounts(
                fetched=10, unknown_market=2, excluded_by_rule=3, selected=4, written=4
            )

    def test_error_is_a_value_error_not_an_assert(self):
        assert issubclass(PageAccountingError, ValueError)
        assert not issubclass(PageAccountingError, AssertionError)

    def test_empty_page(self):
        counts = PageCounts(0, 0, 0, 0, 0)
        assert counts.duplicates == 0
