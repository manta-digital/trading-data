"""``trade_status`` without a database (slice 265, Task 5.1).

Decision 10 is enforced here: the rendered text of every statement the
module issues contains no reference to ``kalshi.trades``. The row outcomes of
the four counts are the integration tier's (``test_kalshi_status.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kalshi_support.fake_status_conn import FakeStatusConn

from manta_trading.data.kalshi import trade_status as module
from manta_trading.data.kalshi.constants import TRADE_LAG_STALE_AFTER, Surface
from manta_trading.data.kalshi.selection import (
    CATALOG_JOIN,
    CollectionRule,
    selection_sql,
)
from manta_trading.data.kalshi.trade_status import TradeStatus, read_trade_status

RULE_C = CollectionRule(
    True, frozenset(), frozenset({"Sports", "Mentions"}), "MENTION|SAY", r"\msay\M"
)
NOW = datetime(2026, 8, 28, 15, 24, 11, tzinfo=UTC)


class TestDecisionTen:
    def test_no_statement_references_the_trades_table(self):
        """Every figure is ``sync_state`` plus the catalog join."""
        ever = selection_sql(RULE_C, "ever")
        statements = [
            module.STATE_QUERY.as_string(None),
            module.TRADE_COUNTS.format(
                catalog=CATALOG_JOIN, ever=ever.predicate
            ).as_string(None),
        ]
        for text in statements:
            assert "kalshi.trades" not in text
            assert "trades" not in text.replace("%(surface)s", "")
        assert "kalshi.sync_state" in statements[0]
        assert "kalshi.markets m" in statements[1]

    def test_every_module_level_statement_is_covered(self):
        """A new ``sql.SQL`` constant added to the module must join the test
        above — enumerated so it cannot be forgotten silently."""
        from psycopg import sql

        constants = {
            name for name, value in vars(module).items() if isinstance(value, sql.SQL)
        }
        assert constants == {"STATE_QUERY", "TRADE_COUNTS"}


class TestToDict:
    def test_shape_and_utc(self):
        status = TradeStatus(
            last_phase_at=NOW,
            tape_through=NOW - timedelta(minutes=5),
            lag=timedelta(minutes=8, seconds=30),
            behind=False,
            coverage_from=datetime(2026, 6, 29, tzinfo=UTC),
            complete_through_close=412_010,
            partial_history=6_120,
            short_of_close=310,
            before_coverage=1_203_442,
        )
        payload = status.to_dict()
        assert payload == {
            "last_phase_at": "2026-08-28T15:24:11+00:00",
            "tape_through": "2026-08-28T15:19:11+00:00",
            "lag_minutes": 8,
            "behind": False,
            "coverage_from": "2026-06-29T00:00:00+00:00",
            "complete_through_close": 412_010,
            "partial_history": 6_120,
            "short_of_close": 310,
            "before_coverage": 1_203_442,
            "stale_after_minutes": int(TRADE_LAG_STALE_AFTER.total_seconds() // 60),
        }


class TestEffectiveFloor:
    """Slice 267, Decision 8: ``coverage_from`` bound to the counts is the
    lower of the live floor and the historical watermark."""

    LIVE_FLOOR = datetime(2026, 7, 1, tzinfo=UTC)
    LAG = timedelta(minutes=8)

    def _conn(self, historical: tuple[object, ...] | None) -> FakeStatusConn:
        return FakeStatusConn(
            {
                Surface.TRADES.value: (NOW, NOW - self.LAG, self.LIVE_FLOOR, self.LAG),
                Surface.HISTORICAL.value: historical,
            },
            counts=(1, 2, 3, 4, 10),
        )

    def _bound_floor(self, conn: FakeStatusConn) -> object:
        return next(p["coverage_from"] for p in conn.params if "coverage_from" in p)

    def test_live_floor_without_a_historical_row(self):
        conn = self._conn(None)
        status = read_trade_status(conn.as_connection(), RULE_C)
        assert status is not None and status.coverage_from == self.LIVE_FLOOR
        assert self._bound_floor(conn) == self.LIVE_FLOOR

    def test_live_floor_while_the_historical_watermark_is_unset(self):
        conn = self._conn((NOW, None, None, "archive-3"))
        status = read_trade_status(conn.as_connection(), RULE_C)
        assert status is not None and status.coverage_from == self.LIVE_FLOOR

    def test_the_minimum_with_a_historical_watermark(self):
        below = self.LIVE_FLOOR - timedelta(days=40)
        conn = self._conn((NOW, below, datetime(2026, 1, 1, tzinfo=UTC), None))
        status = read_trade_status(conn.as_connection(), RULE_C)
        assert status is not None and status.coverage_from == below
        assert self._bound_floor(conn) == below
        assert [p["surface"] for p in conn.params if "surface" in p] == [
            Surface.TRADES.value,
            Surface.HISTORICAL.value,
        ]
