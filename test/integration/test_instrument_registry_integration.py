"""
Integration tests for InstrumentRegistry against a real TimescaleDB instance.

Runs on a throwaway database (``ephemeral_db`` + migrations); all tests skip
when MT_TIMESCALE_TEST_URL is not set. Since 2026-08-04 this file must not
read MT_TIMESCALE_DB_URL — it previously registered TEST.* rows into whatever
that URL pointed at, production included.

Run with:
    MT_TIMESCALE_TEST_URL=postgresql://postgres:pw@host:5432/postgres \
        uv run pytest test/integration/test_instrument_registry_integration.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta

import pytest

from manta_trading.data.base.instrument_registry import Instrument, InstrumentRegistry
from manta_trading.providers.types import ProviderType

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

skip_no_db = pytest.mark.skipif(
    not os.environ.get("MT_TIMESCALE_TEST_URL"),
    reason="MT_TIMESCALE_TEST_URL not set — skipping integration tests",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _unique_id() -> str:
    """Return a short unique suffix for test isolation."""
    return uuid.uuid4().hex[:8].upper()


@pytest.fixture()
def registry(instruments_clean_db):
    """Yield an InstrumentRegistry on a throwaway DB at pre-141 schema.

    Pre-141 deliberately: ``register_instrument`` predates slice 141, has no
    production callers, and cannot satisfy the NOT NULL constraints migration
    016 added (it never writes ``eodhd_type``). On the current schema every
    call raises NotNullViolation — these tests only ever passed against a
    database in the pre-141 state. No cleanup fixture needed: the database is
    dropped after each test.
    """
    reg = InstrumentRegistry(conninfo=instruments_clean_db)
    yield reg
    reg.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@skip_no_db
def test_register_and_retrieve_by_canonical_id(registry):
    uid = _unique_id()
    canonical_id = f"TEST.{uid}"
    symbol = f"T{uid}"

    inst, was_inserted = registry.register_instrument(
        canonical_id=canonical_id,
        symbol=symbol,
        asset_class="equity",
        venue="NYSE",
    )
    assert was_inserted is True
    assert inst.canonical_id == canonical_id
    assert inst.symbol == symbol
    assert inst.instrument_id is not None

    retrieved = registry.get_by_canonical_id(canonical_id)
    assert retrieved is not None
    assert retrieved.canonical_id == canonical_id
    assert retrieved.instrument_id == inst.instrument_id


@skip_no_db
def test_register_idempotent(registry):
    uid = _unique_id()
    canonical_id = f"TEST.{uid}"
    symbol = f"T{uid}"

    inst1, inserted1 = registry.register_instrument(
        canonical_id=canonical_id, symbol=symbol, asset_class="equity", venue="NYSE"
    )
    inst2, inserted2 = registry.register_instrument(
        canonical_id=canonical_id, symbol=symbol, asset_class="equity", venue="NYSE"
    )
    assert inserted1 is True
    assert inserted2 is False
    assert inst1.instrument_id == inst2.instrument_id


@skip_no_db
def test_get_by_symbol(registry):
    uid = _unique_id()
    canonical_id = f"TEST.{uid}"
    symbol = f"T{uid}"

    registry.register_instrument(
        canonical_id=canonical_id, symbol=symbol, asset_class="equity", venue="NYSE"
    )
    # Invalidate cache so get_by_symbol hits DB
    registry._invalidate_cache()

    retrieved = registry.get_by_symbol(symbol)
    assert retrieved is not None
    assert retrieved.symbol == symbol


@skip_no_db
def test_get_by_symbol_not_found(registry):
    result = registry.get_by_symbol("XYZZY_NO_SUCH_SYMBOL_99999")
    assert result is None


@skip_no_db
def test_provider_mapping_and_lookup(registry):
    uid = _unique_id()
    canonical_id = f"TEST.{uid}"
    symbol = f"T{uid}"

    inst, _ = registry.register_instrument(
        canonical_id=canonical_id, symbol=symbol, asset_class="equity", venue="NYSE"
    )
    registry.update_provider_mapping(
        instrument_id=inst.instrument_id,
        provider=ProviderType.EODHD.value,
        provider_symbol=symbol,
    )
    registry._invalidate_cache()

    # valid_from defaults to NOW(), which compares as later than midnight-today
    # when as_of_date is cast to timestamptz; query with tomorrow to step past
    # the freshly-inserted mapping's window edge.
    retrieved = registry.get_by_provider_symbol(
        provider=ProviderType.EODHD.value,
        provider_symbol=symbol,
        as_of_date=date.today() + timedelta(days=1),
    )
    assert retrieved is not None
    assert retrieved.instrument_id == inst.instrument_id


@skip_no_db
def test_list_instruments_filtered(registry):
    uid = _unique_id()
    sym_nyse = f"T{uid}N"
    sym_nasdaq = f"T{uid}Q"

    registry.register_instrument(
        canonical_id=f"TEST.{uid}N",
        symbol=sym_nyse,
        asset_class="equity",
        venue="NYSE",
    )
    registry.register_instrument(
        canonical_id=f"TEST.{uid}Q",
        symbol=sym_nasdaq,
        asset_class="equity",
        venue="NASDAQ",
    )

    nyse_instruments = registry.list_instruments(venue="NYSE")
    nyse_symbols = {i.symbol for i in nyse_instruments}
    assert sym_nyse in nyse_symbols
    assert sym_nasdaq not in nyse_symbols


@skip_no_db
def test_update_provider_mapping_idempotent(registry):
    uid = _unique_id()
    canonical_id = f"TEST.{uid}"
    symbol = f"T{uid}"

    inst, _ = registry.register_instrument(
        canonical_id=canonical_id, symbol=symbol, asset_class="equity", venue="NYSE"
    )
    # Call twice — should not raise
    registry.update_provider_mapping(inst.instrument_id, ProviderType.EODHD.value, symbol)
    registry.update_provider_mapping(inst.instrument_id, ProviderType.EODHD.value, symbol)


# ---------------------------------------------------------------------------
# upsert_eodhd_universe — Task 3.4 (slice 141)
# ---------------------------------------------------------------------------

def _eodhd_row(
    symbol: str,
    canonical_id: str,
    venue: str = "US",
    eodhd_exchange: str = "US",
    eodhd_type: str = "Common Stock",
    trading_calendar_id: str = "NYSE",
    delisted: bool = False,
) -> dict:
    return {
        "canonical_id": canonical_id,
        "symbol": symbol,
        "asset_class": "equity",
        "venue": venue,
        "currency": "USD",
        "trading_calendar_id": trading_calendar_id,
        "eodhd_type": eodhd_type,
        "eodhd_exchange": eodhd_exchange,
        "delisted_at_eodhd": delisted,
    }


@skip_no_db
def test_upsert_eodhd_universe_inserts_new_symbol(instruments_clean_db):
    """Upserting one new symbol returns (1, 0, 0)."""
    registry = InstrumentRegistry(conninfo=instruments_clean_db)
    try:
        row = _eodhd_row("XYZ", "XYZ.US")
        result = registry.upsert_eodhd_universe([row])
        assert result == (1, 0, 0)
    finally:
        registry.close()


@skip_no_db
def test_upsert_eodhd_universe_idempotent(instruments_clean_db):
    """Re-running same payload returns (0, 0, 1) — unchanged."""
    registry = InstrumentRegistry(conninfo=instruments_clean_db)
    try:
        row = _eodhd_row("XYZ", "XYZ.US")
        registry.upsert_eodhd_universe([row])
        result = registry.upsert_eodhd_universe([row])
        assert result == (0, 0, 1)
    finally:
        registry.close()


@skip_no_db
def test_upsert_eodhd_universe_does_not_overwrite_venue(instruments_clean_db):
    """Existing row's venue and canonical_id are preserved during upsert."""
    registry = InstrumentRegistry(conninfo=instruments_clean_db)
    try:
        # Seed a row with authoritative venue (simulates AV-seeded baseline)
        registry.register_instrument(
            canonical_id="AAPL.NASDAQ",
            symbol="AAPL",
            asset_class="equity",
            venue="NASDAQ",
        )
        # Upsert EODHD data for the same symbol — new canonical_id would be AAPL.US
        # but it should match existing AAPL.NASDAQ and NOT overwrite it
        row = _eodhd_row("AAPL", "AAPL.NASDAQ", venue="US", eodhd_exchange="US")
        registry.upsert_eodhd_universe([row])
        registry._invalidate_cache()

        inst = registry.get_by_canonical_id("AAPL.NASDAQ")
        assert inst is not None
        assert inst.venue == "NASDAQ"
        assert inst.canonical_id == "AAPL.NASDAQ"
        assert inst.eodhd_type == "Common Stock"
    finally:
        registry.close()


@skip_no_db
def test_upsert_eodhd_universe_get_by_symbol_works(instruments_clean_db):
    """After upsert, get_by_symbol returns the row (success criterion 7)."""
    registry = InstrumentRegistry(conninfo=instruments_clean_db)
    try:
        row = _eodhd_row("AAPL", "AAPL.NASDAQ", venue="NASDAQ", eodhd_exchange="US",
                         trading_calendar_id="NASDAQ")
        registry.upsert_eodhd_universe([row])
        registry._invalidate_cache()
        inst = registry.get_by_symbol("AAPL")
        assert inst is not None
        assert inst.symbol == "AAPL"
    finally:
        registry.close()
