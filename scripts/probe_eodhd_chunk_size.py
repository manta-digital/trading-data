"""
EODHD chunk-size feasibility probe.

Goal: confirm or refute the documented 120-day cap on /intraday with
interval=1m. Tests progressively larger windows for one liquid symbol
and records: HTTP status, returned date span, bar count, response time,
and whether the server truncates silently or errors on overshoot.

Run:
    uv run python scripts/probe_eodhd_chunk_size.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["MT_EODHD_API_KEY"]
BASE = "https://eodhd.com/api"
SYMBOL = "AAPL.US"

# End the windows at a known-recent business day so we are not racing the
# 2-3 hour finalization delay. 2025-01-15 was a Wednesday in regular session.
END_DATE = date(2025, 1, 15)

# Window sizes (calendar days) to test.
WINDOW_DAYS = [30, 60, 90, 110, 119, 120, 121, 130, 150, 180, 240, 365]

OUT_DIR = Path("project-documents/user/research/eodhd-chunk-size-probe")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _redact(url: str) -> str:
    return url.replace(API_KEY, "***")


def _probe_window(client: httpx.Client, days: int) -> dict:
    end = datetime.combine(END_DATE, datetime.min.time(), tzinfo=timezone.utc)
    end = end.replace(hour=23, minute=59, second=59)
    start = end - timedelta(days=days)

    params = {
        "interval": "1m",
        "from": int(start.timestamp()),
        "to": int(end.timestamp()),
        "api_token": API_KEY,
        "fmt": "json",
    }

    t0 = time.monotonic()
    try:
        r = client.get(f"{BASE}/intraday/{SYMBOL}", params=params, timeout=60.0)
        elapsed = time.monotonic() - t0
    except (httpx.RequestError, httpx.HTTPError) as exc:
        return {
            "requested_days": days,
            "status": "exception",
            "error": str(exc),
            "elapsed_s": time.monotonic() - t0,
        }

    result = {
        "requested_days": days,
        "requested_from": start.isoformat(),
        "requested_to": end.isoformat(),
        "http_status": r.status_code,
        "elapsed_s": round(elapsed, 2),
        "rate_limit_remaining": r.headers.get("X-RateLimit-Remaining"),
        "response_size_kb": round(len(r.content) / 1024, 1),
    }

    # Try to parse JSON regardless of status.
    try:
        body = r.json()
    except json.JSONDecodeError:
        result["body_preview"] = r.text[:300]
        return result

    if r.status_code != 200:
        result["error_body"] = body
        return result

    if not isinstance(body, list):
        result["body_preview"] = json.dumps(body)[:300]
        return result

    result["bar_count"] = len(body)
    if body:
        first = body[0]
        last = body[-1]
        result["first_bar_dt"] = first.get("datetime")
        result["last_bar_dt"] = last.get("datetime")
        first_ts = first.get("timestamp", 0)
        last_ts = last.get("timestamp", 0)
        if first_ts and last_ts:
            result["actual_span_days"] = round((last_ts - first_ts) / 86400, 2)
            result["coverage_ratio"] = (
                round(result["actual_span_days"] / days, 3) if days else None
            )

    return result


def main() -> int:
    print(f"\nProbing EODHD /intraday window sizes for {SYMBOL}")
    print(f"All windows end at {END_DATE.isoformat()} 23:59:59 UTC\n")

    results: list[dict] = []
    with httpx.Client() as client:
        for days in WINDOW_DAYS:
            print(f"=== Requesting {days}-day window ===")
            r = _probe_window(client, days)
            results.append(r)

            status = r.get("http_status")
            if status == 200:
                print(
                    f"  OK  bars={r.get('bar_count'):>7,}  "
                    f"size={r.get('response_size_kb'):>7,.1f}KB  "
                    f"span={r.get('actual_span_days')}d "
                    f"({r.get('coverage_ratio')}x of requested)  "
                    f"elapsed={r.get('elapsed_s')}s"
                )
                print(
                    f"      first={r.get('first_bar_dt')}  "
                    f"last={r.get('last_bar_dt')}"
                )
            elif status == 402:
                print(f"  402 Payment Required  err={r.get('error_body')}")
            elif status == 422:
                print(f"  422 Unprocessable    err={r.get('error_body')}")
            elif status:
                print(f"  HTTP {status}  body={r.get('error_body') or r.get('body_preview')}")
            else:
                print(f"  EXCEPTION: {r.get('error')}")

            # Be nice to the API; spread requests slightly.
            time.sleep(0.3)

    out_path = OUT_DIR / "results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nDetailed results: {out_path}")

    # Summary table.
    print("\n=== Summary ===")
    print(f"{'requested':>10}  {'status':>6}  {'bars':>9}  {'span':>8}  {'cov':>5}  size")
    for r in results:
        status = r.get("http_status", "EXC")
        bars = r.get("bar_count", "-")
        span = r.get("actual_span_days", "-")
        cov = r.get("coverage_ratio", "-")
        size = f"{r.get('response_size_kb', 0):.1f}KB" if r.get("response_size_kb") else "-"
        bars_s = f"{bars:>9,}" if isinstance(bars, int) else f"{bars:>9}"
        span_s = f"{span:>7}d" if isinstance(span, (int, float)) else f"{span:>8}"
        cov_s = f"{cov:>5}" if isinstance(cov, (int, float)) else f"{cov:>5}"
        print(f"{r['requested_days']:>10}d  {str(status):>6}  {bars_s}  {span_s}  {cov_s}  {size}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
