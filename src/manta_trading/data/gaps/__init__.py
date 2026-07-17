"""manta_trading.data.gaps — data-gap computation and maintenance.

Public surface re-exported here for convenient import:

    from manta_trading.data.gaps import (
        GapRange,
        compute_missing_ranges,
        group_sessions_into_ranges,
        next_trading_session_after,
        UpdateResult,
        update_data_gaps,
        coalesce_data_gaps,
    )
"""

from __future__ import annotations

from manta_trading.data.gaps.actionable_gap_selector import GapRow, pick_most_recent_actionable_gap
from manta_trading.data.gaps.coalesce_data_gaps import coalesce_data_gaps
from manta_trading.data.gaps.compute_missing_ranges import (
    GapRange,
    compute_missing_ranges,
    group_sessions_into_ranges,
)
from manta_trading.data.gaps.next_trading_session_after import next_trading_session_after
from manta_trading.data.gaps.update_data_gaps import UpdateResult, update_data_gaps

__all__ = [
    "GapRange",
    "GapRow",
    "UpdateResult",
    "coalesce_data_gaps",
    "compute_missing_ranges",
    "group_sessions_into_ranges",
    "next_trading_session_after",
    "pick_most_recent_actionable_gap",
    "update_data_gaps",
]
