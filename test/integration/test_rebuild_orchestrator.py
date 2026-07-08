"""Integration tests for the rebuild orchestrator (slice 141, Task 11.2).

Uses respx to mock EODHD and Finnhub HTTP responses, and a real TimescaleDB
instruments table (via the instruments_clean_db fixture).

Run with:
    MT_TIMESCALE_DB_URL=postgresql://... uv run pytest \
        test/integration/test_rebuild_orchestrator.py -v
"""

from __future__ import annotations

import json

import psycopg
import pytest
import respx
from httpx import Response

from manta_trading.data.universe.rebuild import run_rebuild

_EODHD_BASE = "https://eodhd.com/api"
_FINNHUB_BASE = "https://finnhub.io/api/v1"

# ─── Fixture data ────────────────────────────────────────────────────────────

def _equity(code: str, type_: str = "Common Stock") -> dict:
    return {"Code": code, "Name": code, "Country": "USA", "Exchange": "US",
            "Currency": "USD", "Type": type_}

def _index(code: str) -> dict:
    return {"Code": code, "Name": code, "Country": "USA", "Exchange": "INDX",
            "Currency": "USD", "Type": "INDEX"}

# Active US: mix of types, one mutual fund (filtered out)
_ACTIVE_US = [
    _equity("AAPL", "Common Stock"),
    _equity("MSFT", "Common Stock"),
    _equity("SPY", "ETF"),
    _equity("MF1", "Mutual Fund"),   # ← must be filtered out
]

_DELISTED_US = [
    _equity("DEAD1", "Common Stock"),
]

_INDX = [
    _index("SPX"),
    {"Code": "FTSE", "Name": "FTSE", "Country": "GBR", "Exchange": "INDX",  # ← filtered (not USA)
     "Currency": "GBP", "Type": "INDEX"},
]

_AAPL_PROFILE = {
    "ipo": "1980-12-12",
    "exchange": "NASDAQ NMS - GLOBAL MARKET",
    "ticker": "AAPL",
}

def _mock_eodhd_routes():
    respx.get(_EODHD_BASE + "/exchange-symbol-list/US").mock(
        side_effect=lambda req: Response(
            200,
            json=_DELISTED_US if "delisted=1" in str(req.url) else _ACTIVE_US,
        )
    )
    respx.get(_EODHD_BASE + "/exchange-symbol-list/INDX").mock(
        return_value=Response(200, json=_INDX)
    )


def _mock_finnhub_routes(aapl_profile: dict | None = None):
    profile = aapl_profile if aapl_profile is not None else _AAPL_PROFILE
    respx.get(_FINNHUB_BASE + "/stock/profile2").mock(
        side_effect=lambda req: Response(
            200,
            json=profile if "AAPL" in str(req.url) else {"ipo": "", "exchange": ""},
        )
    )


# ─── Tests ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_rebuild_inserts_rows(instruments_clean_db):
    """Full run inserts expected rows; eodhd_type IS NULL = 0 after run."""
    _mock_eodhd_routes()
    _mock_finnhub_routes()

    summary = await run_rebuild(
        db_url=instruments_clean_db,
        skip_finnhub=True,
        eodhd_api_key="TESTKEY",
        finnhub_api_key="TESTKEY",
    )

    # Active US: AAPL, MSFT (Common Stock), SPY (ETF)
    # Delisted: DEAD1
    # INDX USA: SPX
    # MF1 and FTSE are filtered out
    assert summary["inserted"] == 5
    assert summary["updated"] == 0

    with psycopg.connect(instruments_clean_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM instruments WHERE eodhd_type IS NULL").fetchone()[0]
        assert count == 0


@pytest.mark.asyncio
@respx.mock
async def test_rebuild_idempotent(instruments_clean_db):
    """Second run with same data: inserted=0, updated=0."""
    _mock_eodhd_routes()
    _mock_finnhub_routes()

    await run_rebuild(
        db_url=instruments_clean_db, skip_finnhub=True,
        eodhd_api_key="TESTKEY", finnhub_api_key="TESTKEY",
    )

    _mock_eodhd_routes()
    summary = await run_rebuild(
        db_url=instruments_clean_db, skip_finnhub=True,
        eodhd_api_key="TESTKEY", finnhub_api_key="TESTKEY",
    )
    assert summary["inserted"] == 0
    assert summary["updated"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_rebuild_preserves_av_seeded_venues(instruments_clean_db):
    """AV-seeded AAPL venue/canonical_id must not change after rebuild."""
    # Seed AAPL with authoritative NASDAQ venue
    with psycopg.connect(instruments_clean_db) as conn:
        conn.execute(
            """
            INSERT INTO instruments (canonical_id, symbol, asset_class, venue, currency,
                trading_calendar_id, adjustment_policy)
            VALUES ('AAPL.NASDAQ', 'AAPL', 'equity', 'NASDAQ', 'USD', 'NASDAQ', 'split_adjusted')
            """
        )
        conn.commit()

    _mock_eodhd_routes()
    await run_rebuild(
        db_url=instruments_clean_db, skip_finnhub=True,
        eodhd_api_key="TESTKEY", finnhub_api_key="TESTKEY",
    )

    with psycopg.connect(instruments_clean_db) as conn:
        row = conn.execute(
            "SELECT venue, canonical_id FROM instruments WHERE symbol='AAPL'"
        ).fetchone()
    assert row is not None
    assert row[0] == "NASDAQ"
    assert row[1] == "AAPL.NASDAQ"


@pytest.mark.asyncio
@respx.mock
async def test_rebuild_dry_run_no_mutation(instruments_clean_db):
    """--dry-run leaves row count unchanged."""
    _mock_eodhd_routes()
    _mock_finnhub_routes()

    with psycopg.connect(instruments_clean_db) as conn:
        before = conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]

    summary = await run_rebuild(
        db_url=instruments_clean_db, dry_run=True,
        eodhd_api_key="TESTKEY", finnhub_api_key="TESTKEY",
    )
    assert summary["dry_run"] is True

    with psycopg.connect(instruments_clean_db) as conn:
        after = conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
    assert after == before


@pytest.mark.asyncio
@respx.mock
async def test_rebuild_applies_migrations_015_016_017(instruments_clean_db):
    """After a full run, migrations 015/016/017 are applied."""
    _mock_eodhd_routes()
    await run_rebuild(
        db_url=instruments_clean_db, skip_finnhub=True,
        eodhd_api_key="TESTKEY", finnhub_api_key="TESTKEY",
    )

    with psycopg.connect(instruments_clean_db) as conn:
        applied = {
            r[0] for r in conn.execute("SELECT migration_id FROM schema_migrations").fetchall()
        }
    assert "015_instruments_lifecycle_columns" in applied
    assert "016_instruments_eodhd_type_not_null" in applied
    assert "017_instruments_drop_active" in applied


@pytest.mark.asyncio
@respx.mock
async def test_rebuild_eodhd_403_halts_cleanly(instruments_clean_db):
    """EODHD 403 on preflight → EodhdAccessError raised; DB unchanged."""
    from manta_trading.data.universe.eodhd_symbol_list_client import EodhdAccessError

    respx.get(_EODHD_BASE + "/exchange-symbol-list/US").mock(
        return_value=Response(403, text="Forbidden")
    )

    with psycopg.connect(instruments_clean_db) as conn:
        before = conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]

    with pytest.raises(EodhdAccessError):
        await run_rebuild(
            db_url=instruments_clean_db,
            eodhd_api_key="BADKEY", finnhub_api_key="TESTKEY",
        )

    with psycopg.connect(instruments_clean_db) as conn:
        after = conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
    assert after == before


@pytest.mark.asyncio
@respx.mock
async def test_rebuild_finnhub_403_does_not_halt(instruments_clean_db):
    """Finnhub 403 → EODHD upsert + migrations complete; exit 0."""
    _mock_eodhd_routes()
    respx.get(_FINNHUB_BASE + "/stock/profile2").mock(
        return_value=Response(403, text="Forbidden")
    )

    summary = await run_rebuild(
        db_url=instruments_clean_db,
        eodhd_api_key="TESTKEY", finnhub_api_key="TESTKEY",
    )

    assert summary["inserted"] > 0
    with psycopg.connect(instruments_clean_db) as conn:
        applied = {
            r[0] for r in conn.execute("SELECT migration_id FROM schema_migrations").fetchall()
        }
    assert "017_instruments_drop_active" in applied


@pytest.mark.asyncio
@respx.mock
async def test_rebuild_indx_rows_have_indx_venue(instruments_clean_db):
    """INDX-source rows: venue='INDX', canonical_id='{sym}.INDX'; not enriched by Finnhub."""
    _mock_eodhd_routes()
    await run_rebuild(
        db_url=instruments_clean_db, skip_finnhub=True,
        eodhd_api_key="TESTKEY", finnhub_api_key="TESTKEY",
    )

    with psycopg.connect(instruments_clean_db) as conn:
        row = conn.execute(
            "SELECT venue, canonical_id FROM instruments WHERE symbol='SPX'"
        ).fetchone()
    assert row is not None
    assert row[0] == "INDX"
    assert row[1] == "SPX.INDX"


@pytest.mark.asyncio
@respx.mock
async def test_rebuild_disappearing_symbol_deleted_as_orphan(instruments_clean_db):
    """Symbol present on run 1, absent from EODHD on run 2 → deleted as orphan."""
    # Run 1: EODHD includes AAPL and EXTRA
    active_run1 = _ACTIVE_US + [_equity("EXTRA", "Common Stock")]
    respx.get(_EODHD_BASE + "/exchange-symbol-list/US").mock(
        side_effect=lambda req: Response(
            200, json=[] if "delisted=1" in str(req.url) else active_run1
        )
    )
    respx.get(_EODHD_BASE + "/exchange-symbol-list/INDX").mock(
        return_value=Response(200, json=[])
    )
    await run_rebuild(
        db_url=instruments_clean_db, skip_finnhub=True,
        eodhd_api_key="TESTKEY", finnhub_api_key="TESTKEY",
    )

    # Run 2: EODHD no longer includes EXTRA
    _mock_eodhd_routes()
    summary2 = await run_rebuild(
        db_url=instruments_clean_db, skip_finnhub=True,
        eodhd_api_key="TESTKEY", finnhub_api_key="TESTKEY",
    )
    assert summary2["orphans_deleted"] >= 1

    with psycopg.connect(instruments_clean_db) as conn:
        row = conn.execute("SELECT 1 FROM instruments WHERE symbol='EXTRA'").fetchone()
    assert row is None


@pytest.mark.asyncio
@respx.mock
async def test_rebuild_json_summary_keys(instruments_clean_db):
    """Summary dict contains all canonical keys (basis for --json CLI output)."""
    _mock_eodhd_routes()
    summary = await run_rebuild(
        db_url=instruments_clean_db, skip_finnhub=True,
        eodhd_api_key="TESTKEY", finnhub_api_key="TESTKEY",
    )

    for key in ("inserted", "updated", "unchanged", "orphans_deleted",
                "finnhub_populated", "finnhub_not_found", "finnhub_errors"):
        assert key in summary, f"Missing summary key: {key}"

    # Verify it serialises cleanly to JSON
    json_str = json.dumps(summary)
    parsed = json.loads(json_str)
    assert parsed["inserted"] == summary["inserted"]
