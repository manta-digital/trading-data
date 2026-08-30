"""``trade_status`` without a database (slice 265, Task 5.1).

Decision 10 is enforced here: the rendered text of every statement the
module issues contains no reference to ``kalshi.trades``. The row outcomes of
the four counts are the integration tier's (``test_kalshi_status.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from manta_trading.data.kalshi import trade_status as module
from manta_trading.data.kalshi.constants import TRADE_LAG_STALE_AFTER
from manta_trading.data.kalshi.selection import (
    CATALOG_JOIN,
    CollectionRule,
    selection_sql,
)
from manta_trading.data.kalshi.trade_status import TradeStatus

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
