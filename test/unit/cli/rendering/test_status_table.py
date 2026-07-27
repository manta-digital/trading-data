"""Unit tests for status_table rendering module (T5)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from rich.console import Console
from rich.table import Table

from manta_trading.cli.rendering.status_table import (
    COVERAGE_STALE_LABEL,
    GapRow,
    HealthStatus,
    StatusReport,
    StatusRow,
    _humanize_ts,
    render_auto_extend_notice,
    render_coverage_notice,
    render_status_detail,
    render_status_footer,
    render_status_summary,
    status_report_to_json,
)
from manta_trading.data.maintenance.auto_extend import AutoExtendResult
from manta_trading.data.maintenance.status_coverage import CoverageFreshness
from manta_trading.market.maintenance.cagg_freshness import (
    FreshnessVerdict,
    StalenessSignal,
)

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


# ---------------------------------------------------------------------------
# Coverage freshness notice + JSON (slice 167 section 6)
# ---------------------------------------------------------------------------


def make_verdict(
    view_name: str = "minute_coverage",
    *,
    is_fresh: bool = True,
    signal: StalenessSignal = StalenessSignal.LAG_EXCEEDS_THRESHOLD,
    lag: timedelta | None = None,
) -> FreshnessVerdict:
    return FreshnessVerdict(
        view_name=view_name,
        is_fresh=is_fresh,
        signals=() if is_fresh else (signal,),
        lag=lag if lag is not None else timedelta(0),
        threshold=timedelta(days=1),
        detail="test verdict",
    )


def test_coverage_notice_none_when_unknown() -> None:
    """No guard result must never render as a reassuring 'fresh'."""
    assert render_coverage_notice(None) is None


def test_coverage_notice_none_when_fresh() -> None:
    coverage = CoverageFreshness(
        verdicts=(make_verdict("minute_coverage"), make_verdict("daily_coverage"))
    )
    assert render_coverage_notice(coverage) is None


def test_coverage_notice_names_only_stale_cagg() -> None:
    coverage = CoverageFreshness(
        verdicts=(
            make_verdict("minute_coverage", is_fresh=False, lag=timedelta(days=3)),
            make_verdict("daily_coverage"),
        )
    )
    notice = render_coverage_notice(coverage)
    assert notice is not None
    assert COVERAGE_STALE_LABEL in notice
    # Must not reuse the HealthStatus.STALE wording — different concept.
    assert "STALE" not in notice
    assert "minute_coverage" in notice
    # The healthy cagg must not be implicated in the warning.
    assert "daily_coverage" not in notice


def test_coverage_notice_reports_signal_and_remediation() -> None:
    coverage = CoverageFreshness(
        verdicts=(
            make_verdict(
                "daily_coverage",
                is_fresh=False,
                signal=StalenessSignal.NOT_SCHEDULED,
                lag=timedelta(days=9),
            ),
        )
    )
    notice = render_coverage_notice(coverage)
    assert notice is not None
    assert StalenessSignal.NOT_SCHEDULED.value in notice
    # Points at the runbook rather than offering to remediate on a read path.
    assert "R2" in notice


def test_coverage_notice_scopes_the_claim_to_affected_columns() -> None:
    """Stale coverage affects bars_summary only; gap/attempt columns are raw."""
    coverage = CoverageFreshness(
        verdicts=(make_verdict("minute_coverage", is_fresh=False),)
    )
    notice = render_coverage_notice(coverage)
    assert notice is not None
    assert "bars" in notice
    assert "unaffected" in notice


def test_json_coverage_is_sibling_of_rows_not_a_column() -> None:
    """D2: the column contract is fixed — freshness must not enter a row."""
    coverage = CoverageFreshness(
        verdicts=(make_verdict("minute_coverage", is_fresh=False),)
    )
    report = make_report()
    report.coverage = coverage
    payload = json.loads(status_report_to_json(report))

    assert payload["coverage"]["is_stale"] is True
    for row in payload["rows"]:
        assert "coverage" not in row
        assert "is_stale" not in row


def test_json_coverage_serializes_timedelta_and_enum() -> None:
    """lag/threshold are timedelta and signals are StrEnum; neither is JSON-native."""
    coverage = CoverageFreshness(
        verdicts=(
            make_verdict(
                "minute_coverage",
                is_fresh=False,
                signal=StalenessSignal.NOT_SCHEDULED,
                lag=timedelta(hours=6),
            ),
        )
    )
    report = make_report()
    report.coverage = coverage
    verdict = json.loads(status_report_to_json(report))["coverage"]["verdicts"][0]

    assert verdict["lag"] == timedelta(hours=6).total_seconds()
    assert verdict["threshold"] == timedelta(days=1).total_seconds()
    assert verdict["signals"] == [StalenessSignal.NOT_SCHEDULED.value]


def test_json_coverage_null_when_absent() -> None:
    payload = json.loads(status_report_to_json(make_report()))
    assert payload["coverage"] is None


def test_json_coverage_is_stale_false_when_fresh() -> None:
    report = make_report()
    report.coverage = CoverageFreshness(verdicts=(make_verdict("minute_coverage"),))
    assert json.loads(status_report_to_json(report))["coverage"]["is_stale"] is False


def test_status_report_coverage_defaults_to_none() -> None:
    """Callers predating the guard still construct a valid report."""
    assert make_report().coverage is None
