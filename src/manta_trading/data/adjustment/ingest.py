"""Persist splits and dividends fetched from a corporate-actions provider.

Slice 128 split this module into:
  * Fetch + parse — :mod:`manta_trading.data.adjustment.providers.eodhd`
    (and any future provider) implementing
    :class:`~manta_trading.data.adjustment.providers.ICorporateActionsProvider`.
  * Persist — this module's :func:`upsert_splits` /
    :func:`upsert_dividends`, which UPSERT into the ``splits``
    and ``dividends`` tables on TimescaleDB.

The producer/consumer split lets a future Polygon/AlphaVantage CA provider
share the same persister.

The :func:`ingest_corporate_actions` convenience wrapper preserves the
slice-127 single-symbol entrypoint used by the ``mt data adjustment
ingest`` CLI; it is now thin glue (fetch via the configured provider,
persist via the helpers below).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from manta_trading.config import Settings
from manta_trading.data.adjustment.providers import (
    Dividend,
    Split,
    build_corporate_actions_provider,
)
from manta_trading.logging import get_logger

_logger = get_logger(__name__)


@dataclass(frozen=True)
class IngestResult:
    """Counts produced by one :func:`ingest_corporate_actions` call.

    ``*_added`` counts rows that did not previously exist for
    ``(symbol, ex_date)``; ``*_updated`` counts rows whose values were
    revised by EODHD since the last fetch (or whose ``fetched_at`` was
    refreshed by the upsert even when values were unchanged — see note).

    Note: PostgreSQL's ``INSERT ... ON CONFLICT ... DO UPDATE`` reports
    every conflicting row as "updated" in ``cmd_status`` regardless of
    whether the values actually changed. The "_updated" counts here
    therefore include rows whose ``fetched_at`` was bumped on a no-op
    refresh; that is the correct behaviour for the operator-facing
    summary (it answers "how many rows did the upsert touch").
    """

    symbol: str
    splits_added: int
    splits_updated: int
    dividends_added: int
    dividends_updated: int


async def ingest_corporate_actions(
    symbol: str,
    *,
    since: date | None = None,
    settings: Settings,
) -> IngestResult:
    """Fetch via the configured CA provider and upsert into the DB.

    Args:
        symbol: ticker — bare (``AAPL``) is auto-suffixed to ``AAPL.US``
            by the provider, or pass an explicit exchange
            (``BMW.XETRA``).
        since: optional lower bound on ``ex_date``. Rows with
            ``ex_date < since`` are skipped. Defaults to ``None`` (full
            history).
        settings: must carry ``timescale_db_url`` and the credentials the
            configured provider requires.

    Returns:
        :class:`IngestResult` with row counts.
    """
    if not settings.timescale_db_url:
        raise RuntimeError(
            "MT_TIMESCALE_DB_URL is not set; required to upsert splits/dividends"
        )

    provider = build_corporate_actions_provider(settings)
    splits = await provider.fetch_splits(symbol)
    dividends = await provider.fetch_dividends(symbol)

    if since is not None:
        splits = [s for s in splits if s.ex_date >= since]
        dividends = [d for d in dividends if d.ex_date >= since]

    db_symbol = splits[0].symbol if splits else (
        dividends[0].symbol if dividends else symbol.split(".")[0]
    )

    splits_added, splits_updated = upsert_splits(
        str(settings.timescale_db_url), splits
    )
    dividends_added, dividends_updated = upsert_dividends(
        str(settings.timescale_db_url), dividends
    )

    return IngestResult(
        symbol=db_symbol,
        splits_added=splits_added,
        splits_updated=splits_updated,
        dividends_added=dividends_added,
        dividends_updated=dividends_updated,
    )


def upsert_splits(
    db_url: str,
    rows: list[Split],
) -> tuple[int, int]:
    """Upsert :class:`Split` records into TimescaleDB. Returns ``(added, updated)`` counts."""
    if not rows:
        return 0, 0

    import psycopg

    params = [
        (s.symbol, s.ex_date, s.ratio_to, s.ratio_from)
        for s in rows
    ]
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO splits
                    (symbol, ex_date, ratio_to, ratio_from, source, fetched_at)
                VALUES (%s, %s, %s, %s, 'eodhd', NOW())
                ON CONFLICT (symbol, ex_date) DO UPDATE SET
                    ratio_to   = EXCLUDED.ratio_to,
                    ratio_from = EXCLUDED.ratio_from,
                    source     = EXCLUDED.source,
                    fetched_at = NOW()
                RETURNING (xmax = 0) AS was_inserted
                """,
                params,
                returning=True,
            )
            results = [cur.fetchone() for _ in params]
        conn.commit()

    added = sum(1 for r in results if r and r[0])
    updated = sum(1 for r in results if r and not r[0])
    return added, updated


def upsert_dividends(
    db_url: str,
    rows: list[Dividend],
) -> tuple[int, int]:
    """Upsert :class:`Dividend` records into TimescaleDB. Returns ``(added, updated)``."""
    if not rows:
        return 0, 0

    import psycopg

    params = [
        (d.symbol, d.ex_date, d.amount, d.currency)
        for d in rows
    ]
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO dividends
                    (symbol, ex_date, amount, currency, source, fetched_at)
                VALUES (%s, %s, %s, %s, 'eodhd', NOW())
                ON CONFLICT (symbol, ex_date) DO UPDATE SET
                    amount     = EXCLUDED.amount,
                    currency   = EXCLUDED.currency,
                    source     = EXCLUDED.source,
                    fetched_at = NOW()
                RETURNING (xmax = 0) AS was_inserted
                """,
                params,
                returning=True,
            )
            results = [cur.fetchone() for _ in params]
        conn.commit()

    added = sum(1 for r in results if r and r[0])
    updated = sum(1 for r in results if r and not r[0])
    return added, updated
