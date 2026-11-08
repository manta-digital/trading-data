"""Cross-tier checks over the family reports (slice 305, D4).

One check is active: :func:`traded_vs_cleared` compares a volume family's traded
volume with CME's cleared volume, through 303's ``volume_check`` unchanged. The
other is a placeholder: :data:`INACTIVE_CROSS_TIER_CHECKS` names it and gives
the reason, and status and accounting list it as not active. There is no
registry; one is introduced when a second check becomes active.

``family_reports`` calls :func:`traded_vs_cleared` after every family is judged,
passing the scopes it already built: a session is checked only if it is
``complete`` in both the volume family and statistics, and sessions are never
judged again here (``checkable_sessions`` stays ``coverage``'s function).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import date
from typing import Final

from manta_trading.data.tick.cross_tier_result import CrossTierResult
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.schema_families import VOLUME_FAMILIES, SchemaFamily
from manta_trading.data.tick.statistics_reads import VolumeClass, volume_check
from manta_trading.data.tick.status_reads import shape_of
from manta_trading.data.tick.tick_status import TickSessionStatus
from manta_trading.data.tick.tick_status_scope import ProductScope, overlaps
from manta_trading.data.tick.universe import TickUniverseEntry

TRADED_VS_CLEARED: Final = "traded_vs_cleared"

#: Check name -> why it does not run. Issue #71 is the ``ohlcv-1s`` slice. To
#: activate the check: delete the entry here, add a function with
#: ``traded_vs_cleared``'s signature that returns nothing unless the entry wants
#: ``BAR_1M`` and the 1s family, and call it from ``family_reports``.
INACTIVE_CROSS_TIER_CHECKS: Final[Mapping[str, str]] = {
    "ohlcv_1s_minute_sum": "needs the ohlcv-1s family",
}


def _complete_dates(scope: ProductScope) -> set[date]:
    return {
        v.session_date for v in scope.verdicts if v.status is TickSessionStatus.COMPLETE
    }


async def _check_family(
    conn: Conn,
    entry: TickUniverseEntry,
    scopes: Mapping[SchemaFamily, ProductScope],
    family: SchemaFamily,
) -> CrossTierResult:
    scope = scopes[family]
    both = _complete_dates(scope) & _complete_dates(scopes[SchemaFamily.STATISTICS])
    sessions = [
        s
        for s in scope.sessions
        if s.session_date in both
        and scope.wanted is not None
        and overlaps(s, scope.wanted)
    ]
    verdicts = await volume_check(
        conn, shape_of(entry), family, entry.product, sessions
    )
    counted = Counter(v.verdict for v in verdicts)
    return CrossTierResult(
        TRADED_VS_CLEARED,
        family,
        **{cls.value: counted.get(cls, 0) for cls in VolumeClass},
    )


async def traded_vs_cleared(
    conn: Conn,
    entry: TickUniverseEntry,
    scopes: Mapping[SchemaFamily, ProductScope],
) -> tuple[CrossTierResult, ...]:
    """One result per wanted volume family, in ``VOLUME_FAMILIES`` order; ``()``
    unless the entry wants statistics and at least one volume family.

    ``scopes`` holds the scope of every wanted family. A volume family's
    sessions are those overlapping its wanted range that are ``complete`` in it
    and in statistics, passed to ``volume_check`` once. The range is every such
    session, not a recent window: the data is historical with fixed ends, so a
    window would never look at 2012.
    """
    if SchemaFamily.STATISTICS not in scopes:
        return ()
    return tuple(
        [
            await _check_family(conn, entry, scopes, family)
            for family in VOLUME_FAMILIES
            if family in scopes
        ]
    )
