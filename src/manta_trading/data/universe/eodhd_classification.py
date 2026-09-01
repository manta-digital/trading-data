"""EODHD type classification for the v1 instrument universe.

EodhdType is the single source of truth for allowed instrument types.
The SQL CHECK constraint in migration 016 is generated from this enum
so the DB constraint and the Python filter cannot drift.
"""

from __future__ import annotations

from enum import StrEnum


class EodhdType(StrEnum):
    """Allowed values for the instruments.eodhd_type column (slice 141, D3).

    Three members: COMMON_STOCK, ETF, INDEX. Preferred Stock removed in slice 157.
    """

    COMMON_STOCK = "Common Stock"
    ETF = "ETF"
    INDEX = "INDEX"


_ALLOWED_TYPES: frozenset[str] = frozenset(t.value for t in EodhdType)

# OTC tiers excluded from the v1 universe. These carry mostly thin-volume
# / shell / international-ADR symbols that would dominate the universe
# (~25k of ~58k rows) without contributing tradable signal.
# To re-enable a tier, remove it from this set and document the reason in
# the slice that re-introduces it.
_EXCLUDED_EXCHANGES: frozenset[str] = frozenset({
    "PINK",      # Pink Open Market — minimal disclosure
    "OTCQX",     # OTC top tier — re-evaluate later if needed
    "OTCQB",     # OTC venture tier
    "OTCGREY",   # Grey Market — no quotes, no market makers
    "OTCCE",     # Caveat Emptor — public-interest concerns
    "OTCMKTS",   # OTC Markets generic
    "OTCBB",     # Bulletin Board — defunct since 2014
    "OTC",       # Generic OTC bucket
    "NMFQS",     # Mutual fund quotation service
})


def filter_v1_universe(rows: list[dict]) -> list[dict]:
    """Keep only rows whose Type is in the v1 universe type set
    AND whose Exchange is not in the OTC-exclusion set.

    Caller must set ``_delisted: bool`` on each row before calling;
    the value is propagated to ``delisted_at_eodhd`` in the output.

    Args:
        rows: Raw dicts from EODHD bulk symbol-list endpoint, each with
              ``Type``, ``Exchange``, and ``_delisted`` fields.

    Returns:
        Filtered list with ``delisted_at_eodhd`` added to each kept row.

    Raises:
        ValueError: If rows is empty.
    """
    if not rows:
        raise ValueError("filter_v1_universe: rows must not be empty")

    result: list[dict] = []
    for row in rows:
        if row.get("Type") not in _ALLOWED_TYPES:
            continue
        if row.get("Exchange") in _EXCLUDED_EXCHANGES:
            continue
        out = dict(row)
        out["delisted_at_eodhd"] = bool(row.get("_delisted", False))
        result.append(out)
    return result
