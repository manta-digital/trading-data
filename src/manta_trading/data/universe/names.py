"""Refresh ``instruments.name`` from the EODHD symbol lists (issue #60).

The narrow companion to the full rebuild: it fetches the same lists and maps
each row to a canonical_id the same way, then updates only ``name``. No
insert, no orphan delete, no migration, so it is safe on production at any
time. Rows EODHD does not list keep the name they have (NULL if never named).
"""

from __future__ import annotations

from psycopg_pool import ConnectionPool

from manta_trading.api.http_retry import RetryPolicy
from manta_trading.data.base.instrument_registry import InstrumentRegistry
from manta_trading.data.universe.eodhd_symbol_list_client import (
    EodhdSymbolListClient,
)
from manta_trading.data.universe.rebuild import (
    build_upsert_rows,
    fetch_v1_universe,
    load_existing_by_symbol,
)
from manta_trading.logging import get_logger

_logger = get_logger(__name__)

_NAME_COUNTS_SQL = "SELECT count(*), count(name) FROM instruments"


async def refresh_names(db_url: str, eodhd_api_key: str) -> dict[str, int]:
    """Write EODHD names onto existing instruments; return the counts.

    Raises:
        EodhdAccessError: If the EODHD pre-flight fails (no DB write).
    """
    client = EodhdSymbolListClient(api_key=eodhd_api_key, http_policy=RetryPolicy())
    await client.preflight()
    rows = await fetch_v1_universe(client)

    pool: ConnectionPool = ConnectionPool(db_url, min_size=1, max_size=2, open=True)
    registry = InstrumentRegistry(db_url)
    try:
        upserts = build_upsert_rows(rows, load_existing_by_symbol(pool))
        names = {row["canonical_id"]: row["name"] for row in upserts}
        changed = registry.update_names(names)
        with pool.connection() as conn:
            total, named = conn.execute(_NAME_COUNTS_SQL).fetchone()  # type: ignore[misc]
    finally:
        registry.close()
        pool.close()

    summary = {
        "eodhd_rows": len(rows),
        "names_changed": changed,
        "instruments": total,
        "named": named,
        "unnamed": total - named,
    }
    _logger.info("Name refresh: %s", summary)
    return summary
