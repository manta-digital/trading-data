"""Candle-phase value types shared by the config layer, planner, core, status.

``CandleRule`` is the parsed collection rule (design 264, Decision 2). It is
built in exactly one place — ``Settings.candle_rule()`` — and rendered to SQL
in exactly one place — ``CandleRepository.selection_sql``. This module imports
nothing from the client, the repository, psycopg, or the config layer, so the
config layer can import it without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

_SEP = " · "
_ALL = "all"
_NONE = "none"


@dataclass(frozen=True)
class CandleRule:
    """The collection rule: a market is selected when **all** clauses hold.

    Evaluation order (Decision 2): the allow-list when non-empty, then the
    exclude-list, then the two patterns, then the traded clause. A category
    named in both lists is excluded — exclude wins.

    ``categories`` / ``excluded_categories`` hold Kalshi ``series.category``
    values (the venue's vocabulary, so data rather than an enum); an empty
    allow-list means every category. ``excluded_series_pattern`` is a
    PostgreSQL regex over ``series.ticker`` (case-sensitive) and
    ``excluded_title_pattern`` one over ``series.title`` (case-insensitive);
    ``None`` disables that clause.
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
