"""Check a CME calendar's sessions against Databento ohlcv-1m record counts (301 D8).

Usage::

    uv run python scripts/check_cme_history_counts.py --calendar CME_EQUITY \\
        --parent ES.FUT [--start 2010-06-07] [--end 2019-12-31]

A one-off record of how the pre-2020 calendar was checked, not a tool. For
every session dated in ``[start, end]`` it makes two free metadata calls on
``GLBX.MDP3`` ``ohlcv-1m`` with ``stype_in=parent``:

- inside the session ``[open, close)``: expected more than 0 (a zero is an
  undocumented closure or a provider gap);
- in the gap to the next session ``[close, next_open)``: expected 0 (a count is
  a wrong boundary or a provider anomaly; every such bar would fail
  ``session_boundary`` at ingest).

An ``ohlcv-1m`` bar is stamped at its minute's open, so a bar inside the
session never starts at or after the close.

Window semantics, from the databento-python 0.87 source
(``MetadataHttpAPI.get_record_count``): ``start`` is inclusive and ``end``
exclusive; an integer is nanoseconds since the UNIX epoch, which is what this
script sends, so no date forward-filling applies. The SDK's HTTP timeout is the
class attribute ``BentoHttpAPI.TIMEOUT`` (100 s, used for connect and read);
this script sets it explicitly on its client.

Sessions come from ``TradingCalendar.sessions_between`` against the database
``MT_TIMESCALE_DB_URL`` points to; the script only reads. The Databento key is
loaded from settings, as the tick adapter loads it.

A call that fails prints as a failed window with its error and is never counted
as zero. An authentication or bad-request refusal stops the run. There is no
retry, resume state or concurrency: rerun the failed or remaining range with
``--start`` and ``--end``.

Exit codes: 0 no finding and no failed call; 1 a finding or a failed call;
2 the run stopped (refusal, or a range outside the populated sessions).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, time, timedelta

import databento

from manta_trading.config import Settings
from manta_trading.data.base.session_index import Session
from manta_trading.data.base.trading_calendar import (
    OutOfPopulatedRangeError,
    TradingCalendar,
)
from manta_trading.data.tick.databento.adapter import api_key_env, free_call
from manta_trading.market.schema.seed_cme_hours import CME_DATASET_START
from manta_trading.providers.errors import (
    ProviderAuthError,
    ProviderPermanentError,
    ProviderTransientError,
)

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_STOPPED = 2

DATASET = "GLBX.MDP3"
SCHEMA = "ohlcv-1m"
STYPE_IN = "parent"
METADATA_TIMEOUT_SECONDS = 60
DEFAULT_END = date(2019, 12, 31)
NEXT_SESSION_LOOKAHEAD = timedelta(days=7)
"""How far past ``end`` to read for the session after the last one."""


def _ns(ts: datetime) -> int:
    return int(ts.timestamp()) * 1_000_000_000


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=UTC)


def _sessions(calendar_id: str, start: date, end: date) -> list[Session]:
    """Sessions dated in ``[start, end]``, plus the one after, in order."""
    cal = TradingCalendar(calendar_id, str(Settings().timescale_db_url))
    try:
        sessions = cal.sessions_between(
            _midnight(start), _midnight(end) + NEXT_SESSION_LOOKAHEAD
        )
    finally:
        cal.close()
    in_range = [s for s in sessions if start <= s.session_date <= end]
    after = [s for s in sessions if s.session_date > end][:1]
    return in_range + after


def _count(
    client: databento.Historical, parent: str, start: datetime, end: datetime
) -> int:
    with free_call("get_record_count"):
        count = client.metadata.get_record_count(
            dataset=DATASET,
            symbols=[parent],
            schema=SCHEMA,
            stype_in=STYPE_IN,
            start=_ns(start),
            end=_ns(end),
        )
    return int(count)


def _window(
    client: databento.Historical,
    parent: str,
    label: str,
    bounds: tuple[datetime, datetime],
    expect_trading: bool,
) -> bool:
    """Check one window; print it and return True when it is a finding."""
    start, end = bounds
    window = f"{label} [{start.isoformat()}, {end.isoformat()})"
    try:
        count = _count(client, parent, start, end)
    except ProviderTransientError as exc:
        print(f"FAILED   {window}: {exc}")
        return True
    if expect_trading == (count > 0):
        return False
    kind = "NO-BARS " if expect_trading else "GAP-BARS"
    print(f"{kind} {window}: count={count}")
    return True


def run(calendar_id: str, parent: str, start: date, end: date) -> int:
    settings = Settings()
    key = settings.databento_api_key
    if key is None or not key.strip():
        print(f"{api_key_env()} is not set", file=sys.stderr)
        return EXIT_STOPPED
    client = databento.Historical(key=key)
    client.metadata.TIMEOUT = METADATA_TIMEOUT_SECONDS
    try:
        sessions = _sessions(calendar_id, start, end)
    except OutOfPopulatedRangeError as exc:
        print(f"range {start}..{end}: {exc}", file=sys.stderr)
        return EXIT_STOPPED

    findings = 0
    try:
        for session, following in zip(sessions, [*sessions[1:], None], strict=True):
            if session.session_date > end:
                break
            day = session.session_date.isoformat()
            inside = (session.open_utc, session.close_utc)
            findings += _window(client, parent, f"{day} session", inside, True)
            if following is not None:
                gap = (session.close_utc, following.open_utc)
                findings += _window(client, parent, f"{day} gap", gap, False)
    except (ProviderAuthError, ProviderPermanentError) as exc:
        # A refusal repeats on every call: stop rather than print thousands.
        print(f"STOPPED: {exc}", file=sys.stderr)
        return EXIT_STOPPED
    checked = sum(s.session_date <= end for s in sessions)
    print(f"{calendar_id} {start}..{end}: {checked} sessions, {findings} findings")
    return EXIT_FINDINGS if findings else EXIT_CLEAN


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--calendar", required=True)
    parser.add_argument("--parent", required=True, help="e.g. ES.FUT")
    parser.add_argument("--start", type=date.fromisoformat, default=CME_DATASET_START)
    parser.add_argument("--end", type=date.fromisoformat, default=DEFAULT_END)
    args = parser.parse_args()
    return run(args.calendar, args.parent, args.start, args.end)


if __name__ == "__main__":
    sys.exit(main())
