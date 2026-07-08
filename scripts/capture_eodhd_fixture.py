"""Capture a small real EODHD /intraday response for use as a unit-test
fixture.

Reads ``MT_EODHD_API_KEY`` from the environment / .env, fetches one
trading day of 1-minute bars for AAPL.US, and writes the raw JSON to
``test/fixtures/eodhd/aapl_2025-01-15_day.json``.

One day (~960 bars including ETH, ~150 KB) is enough to exercise:
- canonical column schema
- UTC tz-aware timestamps
- sort order + de-duplication
- dtype coercions

The full 120-day chunk path is exercised by the integration test, not by
this fixture.

Run:
    uv run python scripts/capture_eodhd_fixture.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["MT_EODHD_API_KEY"]
BASE = "https://eodhd.com/api"
SYMBOL = "AAPL.US"
DAY = date(2025, 1, 15)  # Wednesday, regular trading session
OUT_PATH = Path("test/fixtures/eodhd/aapl_2025-01-15_day.json")
MIN_EXPECTED_BARS = 300  # Conservative: even RTH alone is ~390 bars


def main() -> int:
    start_dt = datetime.combine(DAY, time(0, 0, 0), tzinfo=timezone.utc)
    end_dt = datetime.combine(DAY, time(23, 59, 59), tzinfo=timezone.utc)
    params = {
        "interval": "1m",
        "from": int(start_dt.timestamp()),
        "to": int(end_dt.timestamp()),
        "api_token": API_KEY,
        "fmt": "json",
    }
    url = f"{BASE}/intraday/{SYMBOL}"

    with httpx.Client(timeout=60.0) as client:
        r = client.get(url, params=params)
    r.raise_for_status()
    body = r.json()

    if not isinstance(body, list):
        raise SystemExit(
            f"Unexpected response shape: {type(body).__name__}: "
            f"{json.dumps(body)[:300]}"
        )
    if len(body) < MIN_EXPECTED_BARS:
        raise SystemExit(
            f"Fixture sanity check failed: expected >={MIN_EXPECTED_BARS} "
            f"bars, got {len(body)}"
        )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(body))
    size_kb = OUT_PATH.stat().st_size / 1024
    print(
        f"Captured {len(body):,} bars to {OUT_PATH} ({size_kb:.1f} KB)"
    )
    print(f"  first bar: {body[0]['datetime']}")
    print(f"  last  bar: {body[-1]['datetime']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
