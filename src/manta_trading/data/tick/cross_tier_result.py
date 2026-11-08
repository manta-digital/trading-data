"""The result of one cross-tier check for one family (slice 305, D4).

Alone in its module so ``family_report`` (which holds it on ``FamilyStatus``)
and ``cross_tier_checks`` (which builds it from the family reports) both import
it without an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

from manta_trading.data.tick.schema_families import SchemaFamily


@dataclass(frozen=True)
class CrossTierResult:
    """Verdicts per class of one check, over one family's volume (one verdict per
    listed outright and session, as ``coverage`` counts them).

    ``check`` names the check, ``family`` is the family whose traded volume was
    judged. The four counts are named for 303's ``VolumeClass`` values (a test
    pins that), so a result is built as ``CrossTierResult(check, family,
    **counts)``.
    """

    check: str
    family: SchemaFamily
    agree: int
    under: int
    over: int
    no_trades: int
