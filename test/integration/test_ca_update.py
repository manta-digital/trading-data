"""Integration test for mt data ca update --symbol (T24, slice 146).

Verifies idempotent re-ingest: pre/post row counts match (no duplicates
on repeated invocation of full-history per-symbol CA fetch).

Skipped without MT_TIMESCALE_DB_URL and MT_EODHD_API_KEY.
"""

from __future__ import annotations

import os
import subprocess

import psycopg
import pytest

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")
MARKET_URL = os.environ.get("MT_MARKET_DB_URL", "")
EODHD_KEY = os.environ.get("MT_EODHD_API_KEY", "")

pytestmark = pytest.mark.skipif(
    not (TIMESCALE_URL and MARKET_URL and EODHD_KEY),
    reason="MT_TIMESCALE_DB_URL, MT_MARKET_DB_URL, and MT_EODHD_API_KEY required",
)


def _ca_counts(symbol: str) -> tuple[int, int]:
    """Return (splits_count, dividends_count) for symbol from market DB."""
    with psycopg.connect(MARKET_URL) as conn:
        s = conn.execute(
            "SELECT COUNT(*) FROM splits WHERE symbol = %s", (symbol,)
        ).fetchone()[0]
        d = conn.execute(
            "SELECT COUNT(*) FROM dividends WHERE symbol = %s", (symbol,)
        ).fetchone()[0]
    return s, d


class TestCaUpdateSymbol:
    def test_idempotent_reingest_aapl(self) -> None:
        """ca update --symbol AAPL is idempotent: row counts stable on repeat."""
        # First run to establish baseline.
        r1 = subprocess.run(
            ["mt", "data", "ca", "update", "--symbol", "AAPL"],
            capture_output=True, text=True, timeout=120, env=os.environ,
        )
        assert r1.returncode == 0, r1.stderr

        splits_before, divs_before = _ca_counts("AAPL")
        assert splits_before > 0, "expected AAPL splits after ingest"
        assert divs_before > 0, "expected AAPL dividends after ingest"

        # Second run — counts must be identical (idempotent upsert).
        r2 = subprocess.run(
            ["mt", "data", "ca", "update", "--symbol", "AAPL"],
            capture_output=True, text=True, timeout=120, env=os.environ,
        )
        assert r2.returncode == 0, r2.stderr

        splits_after, divs_after = _ca_counts("AAPL")
        assert splits_after == splits_before, (
            f"split count changed: {splits_before} → {splits_after}"
        )
        assert divs_after == divs_before, (
            f"dividend count changed: {divs_before} → {divs_after}"
        )
