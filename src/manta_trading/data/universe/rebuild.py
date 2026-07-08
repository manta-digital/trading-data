"""Universe rebuild orchestrator for slice 141.

Implements the full pipeline:
  1. EODHD pre-flight (fatal)
  2. Finnhub pre-flight (non-fatal; degrades to --skip-finnhub)
  3. Apply migration 015
  4. Fetch and filter EODHD symbol lists
  5. Match existing rows; build upsert dicts per D4 rules
  6. Upsert into instruments
  7. Count AV orphans, report, 5s gate, DELETE
  8. Apply migrations 016 and 017
  9. Finnhub enrichment loop (unless skip_finnhub)
  10. Return summary dict
"""

from __future__ import annotations

import asyncio
from typing import Any

from psycopg_pool import ConnectionPool

from manta_trading.api.finnhub.finnhubapi import FinnhubAccessError, FinnhubClient
from manta_trading.api.http_retry import RetryPolicy
from manta_trading.data.base.instrument_registry import InstrumentRegistry
from manta_trading.data.universe.eodhd_classification import EodhdType, filter_v1_universe
from manta_trading.data.universe.eodhd_symbol_list_client import (
    EodhdSymbolListClient,
)
from manta_trading.data.universe.finnhub_ipo_client import FinnhubIpoClient
from manta_trading.data.universe.venue_mapping import is_non_us_exchange, map_finnhub_exchange
from manta_trading.logging import get_logger
from manta_trading.market.schema.migrations import TRACKS
from manta_trading.market.schema.runner import apply_migrations

_logger = get_logger(__name__)

_ORPHAN_GATE_SECONDS = 5
_FINNHUB_PROBE_SYMBOL = "AAPL"

_MIGRATION_015 = "015_instruments_lifecycle_columns"
_MIGRATION_016 = "016_instruments_eodhd_type_not_null"
_MIGRATION_017 = "017_instruments_drop_active"


def _make_pool(db_url: str) -> ConnectionPool:  # type: ignore[type-arg]
    pool: ConnectionPool = ConnectionPool(db_url, min_size=1, max_size=5, open=True)  # type: ignore[assignment]
    return pool


async def _run_finnhub_enrichment(
    ipo_client: FinnhubIpoClient,
    db_url: str,
    summary: dict[str, Any],
    dry_run: bool,
) -> None:
    """Run the Finnhub enrichment loop against all instruments needing it.

    Selects instruments where first_listing_date IS NULL or venue = 'US',
    fetches IPO/profile data from Finnhub, and updates the row in place.
    Non-US instruments are dropped (cross-listings / ADRs).

    Mutates ``summary`` in-place with finnhub_populated, finnhub_not_found,
    finnhub_errors, and non_us_dropped counts.
    """
    pool = _make_pool(db_url)
    try:
        finnhub_populated = finnhub_not_found = finnhub_errors = non_us_dropped = 0

        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, canonical_id FROM instruments "
                    "WHERE first_listing_date IS NULL OR venue = 'US' "
                    "ORDER BY symbol"
                )
                to_enrich = cur.fetchall()

        _logger.info("Finnhub enrichment: %d rows to process", len(to_enrich))

        for symbol, canonical_id in to_enrich:
            try:
                enrichment = await ipo_client.enrich(symbol)
                if enrichment is None:
                    finnhub_not_found += 1
                    continue

                raw_exchange: str = enrichment.get("raw_exchange", "")
                if is_non_us_exchange(raw_exchange):
                    if not dry_run:
                        with pool.connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "DELETE FROM instruments WHERE canonical_id = %s",
                                    (canonical_id,),
                                )
                            conn.commit()
                    non_us_dropped += 1
                    continue

                new_venue = enrichment["venue"]
                new_calendar = enrichment["trading_calendar_id"]
                new_canonical = f"{symbol}.{new_venue}" if new_venue != "US" else canonical_id

                if not dry_run:
                    with pool.connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE instruments SET
                                    first_listing_date  = COALESCE(first_listing_date, %(ipo)s),
                                    venue               = %(venue)s,
                                    trading_calendar_id = %(calendar)s,
                                    canonical_id        = %(new_canonical_id)s,
                                    updated_at          = NOW()
                                WHERE canonical_id = %(old_canonical_id)s
                                """,
                                {
                                    "ipo": enrichment["first_listing_date"],
                                    "venue": new_venue,
                                    "calendar": new_calendar,
                                    "new_canonical_id": new_canonical,
                                    "old_canonical_id": canonical_id,
                                },
                            )
                        conn.commit()
                finnhub_populated += 1

            except FinnhubAccessError as exc:
                _logger.warning("Finnhub 403 during enrichment loop: %s; stopping Finnhub", exc)
                finnhub_errors += 1
                break
            except Exception as exc:
                _logger.warning("Finnhub enrichment error for %s: %s", symbol, exc)
                finnhub_errors += 1

        summary["non_us_dropped"] = non_us_dropped
        summary["finnhub_populated"] = finnhub_populated
        summary["finnhub_not_found"] = finnhub_not_found
        summary["finnhub_errors"] = finnhub_errors
        _logger.info(
            "Finnhub enrichment done: populated=%d not_found=%d errors=%d non_us_dropped=%d",
            finnhub_populated, finnhub_not_found, finnhub_errors, non_us_dropped,
        )
    finally:
        pool.close()


def _build_upsert_rows(eodhd_rows: list[dict], existing_by_symbol: dict[str, dict]) -> list[dict]:
    """Convert filtered EODHD rows to upsert dicts, applying D4 canonical_id rules.

    - Existing AV-seeded rows: keep their venue/canonical_id/trading_calendar_id.
    - New equity rows (eodhd_exchange='US'): transient venue='US', canonical_id='{sym}.US'.
    - Index rows (eodhd_exchange='INDX'): venue='INDX', canonical_id='{sym}.INDX'.
    """
    rows: list[dict] = []
    for row in eodhd_rows:
        symbol: str = row["Code"]
        eodhd_exchange: str = row.get("Exchange", "US")
        eodhd_type: str = row.get("Type", "")
        currency: str = row.get("Currency", "USD") or "USD"
        delisted_at_eodhd: bool = row.get("delisted_at_eodhd", False)

        existing = existing_by_symbol.get(symbol)
        if existing:
            # Preserve authoritative venue/canonical_id for AV-seeded rows (D4/D5)
            canonical_id = existing["canonical_id"]
            venue = existing["venue"]
            trading_calendar_id = existing["trading_calendar_id"]
        elif eodhd_exchange == "INDX":
            canonical_id = f"{symbol}.INDX"
            venue = "INDX"
            trading_calendar_id = None
        else:
            canonical_id = f"{symbol}.US"
            venue = "US"
            trading_calendar_id = "NYSE"

        rows.append({
            "canonical_id": canonical_id,
            "symbol": symbol,
            "asset_class": "equity" if eodhd_type != EodhdType.INDEX else "index",
            "venue": venue,
            "currency": currency,
            "trading_calendar_id": trading_calendar_id,
            "eodhd_type": eodhd_type,
            "eodhd_exchange": eodhd_exchange,
            "delisted_at_eodhd": delisted_at_eodhd,
        })
    return rows


def _load_existing_by_symbol(pool: ConnectionPool) -> dict[str, dict]:
    """Load existing instruments keyed by symbol for the match-by-symbol step (D4)."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT symbol, canonical_id, venue, trading_calendar_id "
                "FROM instruments"
            )
            rows = cur.fetchall()
    return {r[0]: {"canonical_id": r[1], "venue": r[2], "trading_calendar_id": r[3]} for r in rows}


def _count_and_sample_orphans(
    pool: ConnectionPool, current_canonical_ids: set[str]
) -> tuple[int, list[str]]:
    """Count and sample orphans.

    An orphan is any instruments row that is NOT in the current EODHD payload
    (matched by canonical_id). Two cases (D10 + criterion 11):
      - AV-seeded rows EODHD doesn't know (eodhd_type IS NULL after upsert).
      - Previously-EODHD-known rows that disappeared from current payload.
    """
    ids_list = list(current_canonical_ids)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            if ids_list:
                cur.execute(
                    "SELECT COUNT(*) FROM instruments WHERE NOT (canonical_id = ANY(%s))",
                    (ids_list,),
                )
                count: int = cur.fetchone()[0]  # type: ignore[index]
                cur.execute(
                    "SELECT symbol FROM instruments WHERE NOT (canonical_id = ANY(%s)) LIMIT 20",
                    (ids_list,),
                )
                sample = [r[0] for r in cur.fetchall()]
            else:
                cur.execute("SELECT COUNT(*) FROM instruments")
                count = cur.fetchone()[0]  # type: ignore[index]
                cur.execute("SELECT symbol FROM instruments LIMIT 20")
                sample = [r[0] for r in cur.fetchall()]
    return count, sample


def _delete_orphans(pool: ConnectionPool, current_canonical_ids: set[str]) -> int:
    """DELETE instruments not present in the current EODHD payload."""
    if not current_canonical_ids:
        return 0
    ids_list = list(current_canonical_ids)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM instruments WHERE NOT (canonical_id = ANY(%s))",
                (ids_list,),
            )
            deleted: int = cur.rowcount
        conn.commit()
    return deleted


async def run_rebuild(
    db_url: str,
    dry_run: bool = False,
    skip_finnhub: bool = False,
    only_finnhub: bool = False,
    eodhd_api_key: str = "",
    finnhub_api_key: str = "",
) -> dict[str, Any]:
    """Run the full universe rebuild pipeline.

    Args:
        db_url: PostgreSQL connection URL for the minute (TimescaleDB) database.
        dry_run: If True, compute and print counts without any DB mutations.
        skip_finnhub: If True, skip Finnhub enrichment.
        only_finnhub: If True, skip all EODHD steps and run only Finnhub enrichment.
            Requires existing instruments rows; does not call EODHD at all.
        eodhd_api_key: EODHD API token (not required when only_finnhub=True).
        finnhub_api_key: Finnhub API token.

    Returns:
        Summary dict with keys: inserted, updated, unchanged, orphans_deleted,
        finnhub_populated, finnhub_not_found, finnhub_errors.

    Raises:
        EodhdAccessError: If EODHD pre-flight fails (fatal; no DB mutation).
    """
    policy = RetryPolicy()
    eodhd_client = EodhdSymbolListClient(api_key=eodhd_api_key, http_policy=policy)
    finnhub_client = FinnhubClient(api_key=finnhub_api_key, http_policy=policy)
    ipo_client = FinnhubIpoClient(finnhub_client, map_finnhub_exchange)

    summary: dict[str, Any] = {
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "orphans_deleted": 0,
        "finnhub_populated": 0,
        "finnhub_not_found": 0,
        "finnhub_errors": 0,
        "non_us_dropped": 0,
        "dry_run": dry_run,
    }

    if only_finnhub:
        _logger.info("only_finnhub=True — skipping EODHD steps; running Finnhub enrichment only")
        await _run_finnhub_enrichment(ipo_client, db_url, summary, dry_run)
        return summary

    # ── Step 1: EODHD pre-flight (fatal) ──────────────────────────────────────
    _logger.info("Step 1: EODHD pre-flight")
    await eodhd_client.preflight()

    # ── Step 2: Finnhub pre-flight (non-fatal) ────────────────────────────────
    if not skip_finnhub and finnhub_api_key:
        _logger.info("Step 2: Finnhub pre-flight")
        try:
            result = await finnhub_client.fetch_profile(_FINNHUB_PROBE_SYMBOL)
            if result is None:
                _logger.warning("Finnhub pre-flight: no ipo data for probe symbol; enrichment will proceed but may be limited")
        except FinnhubAccessError as exc:
            _logger.warning("Finnhub pre-flight failed: %s; proceeding with EODHD only (--skip-finnhub semantics)", exc)
            skip_finnhub = True

    pool = _make_pool(db_url)
    registry = InstrumentRegistry(db_url)

    # Slice 141 ships three migrations. The orchestrator applies them in two
    # phases: 015 before the upsert, 016/017 after the upsert + orphan delete.
    # This is enforced by partitioning TRACKS["minute"] here.
    minute_track = TRACKS["minute"]
    pre_141_track = [m for m in minute_track if m["id"] not in (_MIGRATION_016, _MIGRATION_017)]
    post_upsert_track = minute_track  # full list — runner skips already-applied

    try:
        # ── Step 3: Apply migration 015 (and any prior pending) ────────────────
        if not dry_run:
            _logger.info("Step 3: Applying migration 015")
            apply_migrations(pool, pre_141_track)

        # ── Step 4: Fetch and filter EODHD lists ──────────────────────────────
        _logger.info("Step 4: Fetching EODHD symbol lists")
        active_us = await eodhd_client.fetch_active_us()
        delisted_us = await eodhd_client.fetch_delisted_us()
        indx_raw = await eodhd_client.fetch_indx()

        # Mark delisted flag and filter to USA for INDX
        for row in active_us:
            row["_delisted"] = False
        for row in delisted_us:
            row["_delisted"] = True
        indx_usa = [r for r in indx_raw if r.get("Country") == "USA"]
        for row in indx_usa:
            row["_delisted"] = False
            row["Exchange"] = "INDX"

        all_rows = filter_v1_universe(active_us + delisted_us + indx_usa)
        _logger.info("Fetched %d filtered EODHD rows", len(all_rows))

        # Count by type for summary
        type_counts: dict[str, dict[str, int]] = {}
        for row in all_rows:
            t = row.get("Type", "unknown")
            bucket = type_counts.setdefault(t, {"active": 0, "delisted": 0})
            bucket["delisted" if row.get("delisted_at_eodhd") else "active"] += 1
        summary["type_counts"] = type_counts

        if dry_run:
            summary["would_process"] = len(all_rows)
            _logger.info("Dry run: would process %d rows; no DB mutations", len(all_rows))
            return summary

        # ── Step 5: Build upsert rows matching existing by symbol ─────────────
        _logger.info("Step 5: Building upsert dicts")
        existing_by_symbol = _load_existing_by_symbol(pool)
        upsert_rows = _build_upsert_rows(all_rows, existing_by_symbol)

        # ── Step 6: Upsert ────────────────────────────────────────────────────
        _logger.info("Step 6: Upserting %d rows", len(upsert_rows))
        inserted, updated, unchanged = registry.upsert_eodhd_universe(upsert_rows)
        summary["inserted"] = inserted
        summary["updated"] = updated
        summary["unchanged"] = unchanged
        _logger.info("Upsert complete: inserted=%d updated=%d unchanged=%d", inserted, updated, unchanged)

        # ── Step 7: Orphan delete ─────────────────────────────────────────────
        # Orphans = rows not in the current EODHD payload. Covers both:
        #  (a) AV-seeded rows EODHD doesn't know (D10).
        #  (b) Previously-EODHD-known rows now absent (criterion 11).
        _logger.info("Step 7: Checking for orphans (rows not in current EODHD payload)")
        current_canonical_ids = {r["canonical_id"] for r in upsert_rows}
        orphan_count, orphan_sample = _count_and_sample_orphans(pool, current_canonical_ids)
        sample_str = ", ".join(orphan_sample[:20])
        print(f"\nOrphans (rows in DB but absent from current EODHD payload): {orphan_count}")
        if orphan_count > 0:
            print(f"  Sample (first 20): {sample_str}")
            print(f"Deleting orphans in {_ORPHAN_GATE_SECONDS}s... (Ctrl-C to abort)")
            await asyncio.sleep(_ORPHAN_GATE_SECONDS)
            deleted = _delete_orphans(pool, current_canonical_ids)
            summary["orphans_deleted"] = deleted
            _logger.info("Deleted %d orphans", deleted)
        else:
            _logger.info("No orphans to delete")

        # ── Step 8: Apply migrations 016 and 017 ──────────────────────────────
        _logger.info("Step 8: Applying migrations 016 and 017")
        apply_migrations(pool, post_upsert_track)

        # ── Step 9: Finnhub enrichment ────────────────────────────────────────
        if not skip_finnhub:
            _logger.info("Step 9: Finnhub enrichment loop")
            await _run_finnhub_enrichment(ipo_client, db_url, summary, dry_run)
        else:
            _logger.info("Step 9: Finnhub enrichment skipped (--skip-finnhub)")

    finally:
        registry.close()
        pool.close()

    return summary
