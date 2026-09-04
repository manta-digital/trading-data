"""The collection rule — its type and its SQL — in exactly one place.

``CollectionRule`` is the parsed rule (slice 264, Decision 2). Under slice
265's Decision 3 it governs candles **and** trades under a surface-neutral
name: one ``MT_KALSHI_COLLECTION_*`` configuration, one ``Settings.
collection_rule()`` parse point, and one renderer — ``selection_sql`` here.
The candle phase's pending queries, the trades phase's ``write_page``, and
every ``status`` count embed the rendered predicate; no other module and no
test spells a clause of it. Every value — category lists, the
operator-supplied regexes — is a bound parameter, never text in the
statement.

Nothing here imports the client, the repository, or the config layer, so
the config layer can import this module without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from psycopg import sql

_SEP = " · "
_ALL = "all"
_NONE = "none"


@dataclass(frozen=True)
class CollectionRule:
    """The collection rule: a market is selected when **all** clauses hold.

    Evaluation order (Decision 2): the allow-list when non-empty, then the
    exclude-list, then the two patterns, then the traded clause. A category
    named in both lists is excluded — exclude wins.

    ``categories`` / ``excluded_categories`` hold Kalshi ``series.category``
    values (the venue's vocabulary, so data rather than an enum); an empty
    allow-list means every category. ``excluded_series_pattern`` is a
    PostgreSQL regex over ``series.ticker`` (case-sensitive) and
    ``excluded_title_pattern`` one over ``series.title`` (case-insensitive);
    ``None`` disables that clause. ``traded_only`` applies to candles only —
    the trades path renders the rule in the ``"any"`` form (below), because
    a trade is itself proof of trading.
    """

    traded_only: bool
    categories: frozenset[str]
    excluded_categories: frozenset[str]
    excluded_series_pattern: str | None
    excluded_title_pattern: str | None

    def describe(self) -> str:
        """The one-line human form used by the ``status`` block's ``rule``
        line and (after a ``candles rule:`` label) the phase's start log line,
        e.g. ``traded 24h · categories all · excluding Mentions, Sports ·
        patterns 2``. Category sets are sorted, so equal rules render equally.
        """
        traded = "traded 24h" if self.traded_only else "traded any"
        categories = ", ".join(sorted(self.categories)) or _ALL
        excluded = ", ".join(sorted(self.excluded_categories)) or _NONE
        patterns = sum(
            pattern is not None
            for pattern in (self.excluded_series_pattern, self.excluded_title_pattern)
        )
        return _SEP.join(
            (
                traded,
                f"categories {categories}",
                f"excluding {excluded}",
                f"patterns {patterns}",
            )
        )


#: How the traded clause is rendered. ``recent`` tests the live 24 h window
#: (the candle phase's open markets); ``ever`` tests lifetime volume
#: (finalized markets, whose 24 h figure is meaningless once settled — and
#: what ``status`` counts for both surfaces); ``any`` omits the traded clause
#: entirely — the trades write path (265 Decision 3: a trade is proof of
#: trading, so testing a volume column would only drop the market's first
#: trade). ``any`` is therefore not a column; it is the absence of one.
SelectionForm = Literal["recent", "ever", "any"]
_TRADED_COLUMN: dict[SelectionForm, str] = {
    "recent": "volume_24h_fp",
    "ever": "volume_fp",
}
_NO_TRADED_CLAUSE: SelectionForm = "any"


@dataclass(frozen=True)
class Selection:
    """The rendered rule: a predicate over aliases ``m`` (markets) and ``s``
    (series) plus the named parameters it binds."""

    predicate: sql.Composed
    params: dict[str, object]


def selection_sql(rule: CollectionRule, form: SelectionForm) -> Selection:
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
        clauses.append(sql.SQL("s.category = ANY(%(collection_categories)s)"))
        params["collection_categories"] = sorted(rule.categories)
    if rule.excluded_categories:
        clauses.append(
            sql.SQL(
                "COALESCE(s.category, '') <> ALL(%(collection_excluded_categories)s)"
            )
        )
        params["collection_excluded_categories"] = sorted(rule.excluded_categories)
    if rule.excluded_series_pattern is not None:
        clauses.append(sql.SQL("s.ticker !~ %(collection_excluded_series_pattern)s"))
        params["collection_excluded_series_pattern"] = rule.excluded_series_pattern
    if rule.excluded_title_pattern is not None:
        clauses.append(
            sql.SQL("COALESCE(s.title, '') !~* %(collection_excluded_title_pattern)s")
        )
        params["collection_excluded_title_pattern"] = rule.excluded_title_pattern
    # The ``any`` form has no traded clause at all (265 Decision 3): the
    # trades phase classifies a market *because* it just traded, and the
    # catalog's volume columns lag the tape.
    if rule.traded_only and form != _NO_TRADED_CLAUSE:
        clauses.append(
            sql.SQL("COALESCE(m.{}, 0) > 0").format(
                sql.Identifier(_TRADED_COLUMN[form])
            )
        )
    if not clauses:
        return Selection(sql.SQL("({})").format(sql.SQL("TRUE")), params)
    return Selection(sql.SQL("({})").format(sql.SQL(" AND ").join(clauses)), params)


def trades_filter_sql(excluded: frozenset[str]) -> Selection:
    """The trades-tape category filter's membership test (slice 268,
    Decision 3): TRUE when the market's category is named in ``excluded``.
    The embedding statement, not this function, negates or counts the test.

    Parameter name ``trades_excluded_categories`` is disjoint from every
    parameter ``selection_sql`` can emit (all ``collection_*``), so rule and
    filter bind together in one statement. An empty set renders literal
    ``FALSE`` (nothing filtered) so the statement shape is constant across
    configurations. ``COALESCE(s.category, '')`` keeps the test two-valued:
    an uncategorised series is never filtered — ``''`` can only match if an
    operator configured the empty string, which the settings parser drops.
    """
    if not excluded:
        return Selection(sql.Composed([sql.SQL("FALSE")]), {})
    return Selection(
        sql.Composed(
            [sql.SQL("COALESCE(s.category, '') = ANY(%(trades_excluded_categories)s)")]
        ),
        {"trades_excluded_categories": sorted(excluded)},
    )


def describe_trades_filter(excluded: frozenset[str]) -> str:
    """The one spelling of the filter for log and status lines: ``none``
    when empty, else ``excluding Crypto, Sports`` (sorted)."""
    if not excluded:
        return _NONE
    return f"excluding {', '.join(sorted(excluded))}"


#: The three-table catalog join every rule-dependent query runs over —
#: aliases ``m`` (markets), ``e`` (events), ``s`` (series); spelled once.
#: ``CATALOG_TABLES`` is the join without its ``FROM``, so the trades phase
#: can ``LEFT JOIN`` a page of tickers onto it (``trade_repository``);
#: ``CATALOG_JOIN`` is what the candle phase extends with its state table
#: (``candle_selection.MARKET_JOIN``) and what every ``status`` count runs over.
CATALOG_TABLES = sql.SQL(
    "kalshi.markets m "
    "JOIN kalshi.events e ON e.event_ticker = m.event_ticker "
    "JOIN kalshi.series s ON s.ticker = e.series_ticker "
)
CATALOG_JOIN = sql.SQL("FROM ") + CATALOG_TABLES
