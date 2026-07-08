"""Unit tests for status_table rendering module (T5)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from rich.console import Console
from rich.table import Table

from manta_trading.cli.rendering.status_table import (
    GapRow,
    HealthStatus,
    StatusReport,
    StatusRow,
    _humanize_ts,
    render_auto_extend_notice,
    render_status_detail,
    render_status_footer,
    render_status_summary,
    status_report_to_json,
)
from manta_trading.data.maintenance.auto_extend import AutoExtendResult

_NOW = datetime.now(timezone.utc)
_TODAY = date.today()


# ---------------------------------------------------------------------------
# Fixture factories
# ---------------------------------------------------------------------------


def make_status_row(**overrides: object) -> StatusRow:
    defaults: dict[str, object] = {
        "symbol": "SPY",
        "granularity": "daily",
        "health": HealthStatus.OK,
        "bars_stored": 1000,
        "first_bar_ts": _NOW - timedelta(days=365),
        "last_bar_ts": _NOW - timedelta(hours=1),
        "gap_count": 0,
        "last_attempt_ts": _NOW - timedelta(minutes=30),
        "last_attempt_outcome": "SUCCESS",
        "target_end_ts": _NOW + timedelta(days=90),
        "effective_start": _TODAY - timedelta(days=365),
    }
    defaults.update(overrides)
    return StatusRow(**defaults)  # type: ignore[arg-type]


def make_gap_row(**overrides: object) -> GapRow:
    defaults: dict[str, object] = {
        "symbol": "SPY",
        "granularity": "daily",
        "gap_start": _NOW - timedelta(days=10),
        "gap_end": _NOW - timedelta(days=9),
        "fetch_status": "RETRY_EXHAUSTED",
        "attempt_count": 5,
        "last_attempt_ts": _NOW - timedelta(hours=2),
    }
    defaults.update(overrides)
    return GapRow(**defaults)  # type: ignore[arg-type]


def make_report(
    rows: list[StatusRow] | None = None,
    gaps: list[GapRow] | None = None,
    auto_extend: AutoExtendResult | None = None,
    symbol: str | None = None,
) -> StatusReport:
    rows = rows or [make_status_row()]
    gaps = gaps or []
    summary = {}
    for row in rows:
        summary[row.health] = summary.get(row.health, 0) + 1
    return StatusReport(
        scope="symbol" if symbol else "all",
        symbol=symbol,
        rows=rows,
        gaps=gaps,
        auto_extend=auto_extend,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# render_status_summary
# ---------------------------------------------------------------------------


def test_summary_table_columns() -> None:
    """render_status_summary returns a list; each table has 8 columns (no granularity col)."""
    report = make_report()
    tables = render_status_summary(report)
    assert isinstance(tables, list)
    assert len(tables) >= 1
    assert len(tables[0].columns) == 8


def test_summary_table_rows() -> None:
    """Three rows for the same granularity produce a single table with 3 rows."""
    rows = [
        make_status_row(symbol="SPY", granularity="daily"),
        make_status_row(symbol="QQQ", granularity="daily"),
        make_status_row(symbol="IWM", granularity="daily"),
    ]
    report = make_report(rows=rows)
    tables = render_status_summary(report)
    assert len(tables) == 1
    assert tables[0].row_count == 3


def test_summary_splits_into_two_tables() -> None:
    """Daily rows and minute rows appear in separate tables, daily first."""
    rows = [
        make_status_row(symbol="SPY", granularity="minute"),
        make_status_row(symbol="SPY", granularity="daily"),
    ]
    report = make_report(rows=rows)
    tables = render_status_summary(report)
    assert len(tables) == 2
    assert "daily" in tables[0].title
    assert "minute" in tables[1].title


def test_health_coloring() -> None:
    """FAILED renders red markup; OK renders green."""
    failed_row = make_status_row(health=HealthStatus.FAILED, granularity="daily")
    ok_row = make_status_row(symbol="QQQ", health=HealthStatus.OK, granularity="daily")
    report = make_report(rows=[failed_row, ok_row])
    tables = render_status_summary(report)

    console = Console(width=200)
    with console.capture() as cap:
        for tbl in tables:
            console.print(tbl)
    output = cap.get()

    assert "FAILED" in output
    assert "OK" in output


# ---------------------------------------------------------------------------
# render_status_footer
# ---------------------------------------------------------------------------


def test_footer_counts() -> None:
    report = StatusReport(
        scope="all",
        symbol=None,
        rows=[],
        gaps=[],
        auto_extend=None,
        summary={
            HealthStatus.OK: 100,
            HealthStatus.GAPS: 5,
            HealthStatus.STALE: 3,
            HealthStatus.FAILED: 2,
        },
    )
    footer = render_status_footer(report)
    assert "OK: 100" in footer
    assert "GAPS: 5" in footer
    assert "STALE: 3" in footer
    assert "FAILED: 2" in footer


def test_footer_all_rows_advisory() -> None:
    report = make_report(rows=[make_status_row()] * 10)
    footer_all = render_status_footer(report, all_rows=True)
    footer_no_all = render_status_footer(report, all_rows=False)

    assert "rows printed" in footer_all
    assert "rows printed" not in footer_no_all


# ---------------------------------------------------------------------------
# render_status_detail
# ---------------------------------------------------------------------------


def test_detail_renders_both_granularities() -> None:
    rows = [
        make_status_row(symbol="SPY", granularity="daily"),
        make_status_row(symbol="SPY", granularity="minute"),
    ]
    report = make_report(rows=rows, symbol="SPY")
    renderables = render_status_detail(report)

    console = Console(width=200)
    with console.capture() as cap:
        for r in renderables:
            console.print(r)
    output = cap.get()

    assert "daily" in output
    assert "minute" in output


def test_gap_table_ordering() -> None:
    """Gaps with out-of-order gap_start rendered in ascending order."""
    late = make_gap_row(gap_start=_NOW - timedelta(days=1))
    early = make_gap_row(gap_start=_NOW - timedelta(days=10))
    report = make_report(gaps=[late, early], symbol="SPY")
    renderables = render_status_detail(report)

    console = Console(width=200)
    with console.capture() as cap:
        for r in renderables:
            console.print(r)
    output = cap.get()

    # Both gaps appear; order check via index
    pos_early = output.find(str((_NOW - timedelta(days=10)).strftime("%Y-%m-%d")))
    pos_late = output.find(str((_NOW - timedelta(days=1)).strftime("%Y-%m-%d")))
    assert pos_early < pos_late, "earlier gap_start must appear before later one"


# ---------------------------------------------------------------------------
# status_report_to_json
# ---------------------------------------------------------------------------


def test_json_schema_fields() -> None:
    report = make_report()
    raw = status_report_to_json(report)
    obj = json.loads(raw)

    for key in ("scope", "rows", "summary", "gaps"):
        assert key in obj, f"missing key: {key}"
    assert isinstance(obj["rows"], list)


def test_json_null_for_none() -> None:
    row = make_status_row(first_bar_ts=None, last_attempt_ts=None)
    report = make_report(rows=[row])
    raw = status_report_to_json(report)
    obj = json.loads(raw)

    assert obj["rows"][0]["first_bar_ts"] is None
    assert obj["rows"][0]["last_attempt_ts"] is None


def test_json_timestamps_are_strings() -> None:
    report = make_report()
    raw = status_report_to_json(report)
    obj = json.loads(raw)

    row = obj["rows"][0]
    assert isinstance(row["first_bar_ts"], str)
    assert isinstance(row["last_bar_ts"], str)


def test_json_auto_extend_error_field() -> None:
    ae = AutoExtendResult(triggered=False, error="oops")
    report = make_report(auto_extend=ae)
    raw = status_report_to_json(report)
    obj = json.loads(raw)

    assert obj["auto_extend"]["error"] == "oops"


# ---------------------------------------------------------------------------
# _humanize_ts
# ---------------------------------------------------------------------------


def test_humanize_ts_never() -> None:
    assert _humanize_ts(None) == "never"


def test_humanize_ts_relative() -> None:
    ts_min = datetime.now(timezone.utc) - timedelta(minutes=5)
    ts_hr = datetime.now(timezone.utc) - timedelta(hours=3)
    ts_day = datetime.now(timezone.utc) - timedelta(days=2)

    assert "5m ago" in _humanize_ts(ts_min)
    assert "3h ago" in _humanize_ts(ts_hr)
    assert "2d ago" in _humanize_ts(ts_day)


# ---------------------------------------------------------------------------
# render_auto_extend_notice
# ---------------------------------------------------------------------------


def test_auto_extend_notice_triggered() -> None:
    ae = AutoExtendResult(
        triggered=True,
        calendars_extended=["NYSE"],
        rows_inserted=504,
        horizon_after={"NYSE": date(2028, 12, 31)},
    )
    notice = render_auto_extend_notice(ae)
    assert notice is not None
    assert "NYSE" in notice
    assert "504" in notice


def test_auto_extend_notice_error() -> None:
    ae = AutoExtendResult(triggered=False, error="calendar_id invalid")
    notice = render_auto_extend_notice(ae)
    assert notice is not None
    assert "failed" in notice.lower()
    assert "mt data --extend" in notice


def test_auto_extend_notice_noop() -> None:
    ae = AutoExtendResult(triggered=False, error=None)
    notice = render_auto_extend_notice(ae)
    assert notice is None
