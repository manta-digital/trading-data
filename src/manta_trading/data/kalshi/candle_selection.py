"""The collection rule, rendered to SQL — in exactly one place (slice 264).

``selection_sql`` turns a ``CandleRule`` (Decision 2) into a predicate over
the aliases ``m`` (``kalshi.markets``) and ``s`` (``kalshi.series``). The
three pending queries and the two counts in ``candle_repository`` and
``status.read_candle_status`` all embed it; no other module and no test
spells the predicate. Every value — category lists, the operator-supplied
regexes — is a bound parameter, never text in the statement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from psycopg import sql

from manta_trading.data.kalshi.candle_types import CandleRule

#: Which ``traded`` column the rule tests: ``recent`` is the last 24 h (live
#: markets); ``ever`` is lifetime volume (finalized markets, whose 24 h figure
#: is meaningless once settled).
SelectionForm = Literal["recent", "ever"]
_TRADED_COLUMN: dict[SelectionForm, str] = {
    "recent": "volume_24h_fp",
    "ever": "volume_fp",
}


@dataclass(frozen=True)
class Selection:
    """The rendered rule: a predicate over aliases ``m`` (markets) and ``s``
    (series) plus the named parameters it binds."""

    predicate: sql.Composed
    params: dict[str, object]


def selection_sql(rule: CandleRule, form: SelectionForm) -> Selection:
    """The Decision 2 predicate, clause by clause, each omitted when its
    setting is empty so an unset value costs nothing.

    NULL handling (task review F003, measured 2026-08-26): ``series.category``
    and ``series.title`` are nullable and Kalshi serves series with neither.
    ``NOT (category = ANY(...))`` and ``title !~* ...`` are NULL on a NULL
    column, and NULL in a ``WHERE`` drops the row silently — so the two
    exclusion clauses use ``COALESCE(..., '')``, which keeps an uncategorised
    or untitled series: it is neither Sports nor Mentions. The **allow-list
    is the deliberate exception**: ``s.category = ANY(...)`` on NULL is NULL,
    and that is correct — an operator naming the categories they want has
    not named the uncategorised ones. ``series.ticker`` is the primary key
    and needs no COALESCE. The traded clause coalesces NULL volume to 0 so
    the whole predicate (allow-list aside) is two-valued and ``status`` can
    negate it to count the excluded.
    """
    clauses: list[sql.Composable] = []
    params: dict[str, object] = {}
    if rule.categories:
        clauses.append(sql.SQL("s.category = ANY(%(candle_categories)s)"))
        params["candle_categories"] = sorted(rule.categories)
    if rule.excluded_categories:
        clauses.append(
            sql.SQL("COALESCE(s.category, '') <> ALL(%(candle_excluded_categories)s)")
        )
        params["candle_excluded_categories"] = sorted(rule.excluded_categories)
    if rule.excluded_series_pattern is not None:
        clauses.append(sql.SQL("s.ticker !~ %(candle_excluded_series_pattern)s"))
        params["candle_excluded_series_pattern"] = rule.excluded_series_pattern
    if rule.excluded_title_pattern is not None:
        clauses.append(
            sql.SQL("COALESCE(s.title, '') !~* %(candle_excluded_title_pattern)s")
        )
        params["candle_excluded_title_pattern"] = rule.excluded_title_pattern
    if rule.traded_only:
        clauses.append(
            sql.SQL("COALESCE(m.{}, 0) > 0").format(
                sql.Identifier(_TRADED_COLUMN[form])
            )
        )
    if not clauses:
        return Selection(sql.SQL("({})").format(sql.SQL("TRUE")), params)
    return Selection(sql.SQL("({})").format(sql.SQL(" AND ").join(clauses)), params)


#: The join every candle-phase query and every ``status`` count runs over,
#: at the collected period (bound as ``%(period)s``); spelled once.
MARKET_JOIN = sql.SQL(
    "FROM kalshi.markets m "
    "JOIN kalshi.events e ON e.event_ticker = m.event_ticker "
    "JOIN kalshi.series s ON s.ticker = e.series_ticker "
    "LEFT JOIN kalshi.market_candle_state st "
    "ON st.market_ticker = m.ticker AND st.period = %(period)s "
)

#: Finalized with no state row, on either side of the cutoff (``%(cutoff)s``,
#: ``%(finalized)s`` bound by the caller): ``BACKLOG`` is still served live
#: and drains under Decision 6; ``BEHIND_CUTOFF`` is slice 266's input.
BACKLOG_CONDITION = sql.SQL(
    "m.status = %(finalized)s AND m.settlement_ts >= %(cutoff)s "
    "AND st.market_ticker IS NULL"
)
BEHIND_CUTOFF_CONDITION = sql.SQL(
    "m.status = %(finalized)s AND m.settlement_ts < %(cutoff)s "
    "AND st.market_ticker IS NULL"
)
