"""Corporate-action adjustment package (slice 153).

k_factor.py and ingest.py are retained from slice 152.
_adjusted.py provides the adjusted-on-read function added in slice 153.
"""

from __future__ import annotations

from manta_trading.data.adjustment._adjusted import adjusted
from manta_trading.data.adjustment.ingest import (
    IngestResult,
    ingest_corporate_actions,
)
from manta_trading.data.adjustment.k_factor import (
    CaSnapshot,
    Dividend,
    Split,
    compute_k_factor,
    compute_snapshot_id,
)

__all__ = [
    "CaSnapshot",
    "Dividend",
    "IngestResult",
    "Split",
    "adjusted",
    "compute_k_factor",
    "compute_snapshot_id",
    "ingest_corporate_actions",
]
