"""The text-search expressions behind ``kalshi_009_search`` and its reader (#63).

A GIN expression index is used only when the query spells the expression
exactly as the index was built, so the migration and ``serve_search`` both
take their SQL from here — one spelling, one place to change it.

Every document is ``to_tsvector('english', ...)`` over the text a user would
type: titles and subtitles, never the rules prose. Series also index their
category and tags.
"""

from __future__ import annotations

from manta_trading.data.kalshi.constants import MarketStatus

TS_CONFIG = "english"


def _document(*columns: str) -> str:
    joined = " || ' ' || ".join(f"coalesce({column}, '')" for column in columns)
    return f"to_tsvector('{TS_CONFIG}', {joined})"


SERIES_DOC = _document("title", "category", "tags::text")
EVENT_DOC = _document("title", "sub_title")
MARKET_DOC = _document("title", "subtitle", "yes_sub_title", "no_sub_title")

#: Markets are searched only while tradable: the full table is 45 GB and the
#: partial index covers about 2% of it. The predicate is a rendered literal so
#: the planner can match it against the index (a bind parameter would not).
OPEN_MARKET_PREDICATE = f"status = '{MarketStatus.ACTIVE.value}'"
