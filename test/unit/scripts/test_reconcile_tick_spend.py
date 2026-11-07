"""``scripts/reconcile_tick_spend.py`` (slice 304 D5): the pure core and its I/O."""

from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from manta_trading.data.tick.constants import (
    CME_DATASET,
    BatchJobState,
    SType,
    TickSchema,
)
from manta_trading.data.tick.provider import BatchJob, TickRequest
from manta_trading.providers.errors import ProviderTransientError

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
SINCE = datetime(2026, 11, 8, tzinfo=UTC)
D = Decimal


@pytest.fixture(scope="module")
def rec() -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module("reconcile_tick_spend")


def _row(rec: ModuleType, rid: int, job: str | None, est: str, act: str | None) -> Any:
    return rec.ManifestRow(
        rid,
        job,
        "statistics",
        "ES.FUT",
        date(2024, 1, 1),
        date(2024, 2, 1),
        D(est),
        None if act is None else D(act),
    )


def _job(job_id: str, state: BatchJobState = BatchJobState.DONE) -> BatchJob:
    request = TickRequest(
        CME_DATASET,
        ("ES.FUT",),
        SType.PARENT,
        TickSchema.STATISTICS,
        date(2024, 1, 1),
        date(2024, 2, 1),
    )
    return BatchJob(
        job_id, request, state, SINCE, None, None, None, None, None, None, D("1")
    )


def test_all_equal_has_no_failure_and_zero_delta(rec: ModuleType) -> None:
    rows = [_row(rec, 1, "a", "1.5", "1.5"), _row(rec, 2, "b", "2", "2")]
    report = rec.reconcile(rows, [_job("a"), _job("b")], D("7"))
    assert report.failures == ()
    assert report.total_delta == 0
    assert report.total_actual == D("3.5")
    assert report.lines[0].days == 31


def test_an_unheld_provider_job_fails_naming_its_id(rec: ModuleType) -> None:
    report = rec.reconcile(
        [_row(rec, 1, "a", "1", "1")], [_job("a"), _job("zz9")], D("7")
    )
    assert len(report.failures) == 1
    assert "zz9" in report.failures[0]


def test_a_delivered_request_without_actual_fails(rec: ModuleType) -> None:
    report = rec.reconcile([_row(rec, 1, "a", "1", None)], [_job("a")], D("7"))
    assert len(report.failures) == 1
    assert "request 1" in report.failures[0]


def test_a_request_still_processing_without_actual_is_not_a_failure(
    rec: ModuleType,
) -> None:
    jobs = [_job("a", BatchJobState.PROCESSING)]
    assert rec.reconcile([_row(rec, 1, "a", "1", None)], jobs, D("7")).failures == ()


def test_a_non_zero_delta_is_reported_and_totalled_not_failed(rec: ModuleType) -> None:
    rows = [_row(rec, 1, "a", "1", "1.25"), _row(rec, 2, "b", "2", "1.75")]
    report = rec.reconcile(rows, [_job("a"), _job("b")], D("7"))
    assert report.failures == ()
    assert [ln.delta for ln in report.lines] == [D("0.25"), D("-0.25")]
    assert report.total_delta == 0
    assert report.total_estimate == 3


def test_total_over_the_cap_fails_and_exactly_at_the_cap_does_not(
    rec: ModuleType,
) -> None:
    rows = [_row(rec, 1, "a", "3", "3"), _row(rec, 2, "b", "4", "4")]
    jobs = [_job("a"), _job("b")]
    assert rec.reconcile(rows, jobs, D("7")).failures == ()
    over = rec.reconcile(rows, jobs, D("6.99"))
    assert len(over.failures) == 1
    assert "exceeds" in over.failures[0]


# -- command line and I/O (3.3, 3.4) ------------------------------------------


def test_a_missing_cap_or_since_is_refused(rec: ModuleType) -> None:
    for argv in (["--since", "2026-11-08T00:00:00Z"], ["--cap-usd", "7"]):
        with pytest.raises(SystemExit) as exc:
            rec.main(argv)
        assert exc.value.code == 2


def test_an_unreadable_job_list_stops_with_the_error_not_an_empty_list(
    rec: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(*_: object) -> None:
        raise ProviderTransientError("batch.list_jobs timed out")

    monkeypatch.setattr(rec, "read_provider_jobs", boom)
    monkeypatch.setattr(rec, "Settings", lambda: object())
    code = rec.main(["--since", "2026-11-08T00:00:00Z", "--cap-usd", "7"])
    err = capsys.readouterr().err
    assert code == rec.EXIT_STOPPED
    assert "batch.list_jobs timed out" in err
    assert "0 jobs" not in err


class _FakeConn:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, sql: str, params: object = None) -> Any:
        self.statements.append(sql)
        return self

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return [
            (
                1,
                "a",
                "statistics",
                ["ES.FUT"],
                date(2024, 1, 1),
                date(2024, 2, 1),
                D("1"),
                D("1"),
            )
        ]


def test_the_session_is_read_only_first_and_nothing_writes(rec: ModuleType) -> None:
    conn = _FakeConn()
    rows = asyncio.run(rec.read_manifest(conn, SINCE))
    assert conn.statements[0] == rec.READ_ONLY_SQL
    assert len(rows) == 1
    writes = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "DROP", "ALTER")
    assert not [s for s in conn.statements if s.lstrip().upper().startswith(writes)]


def test_the_providers_own_charge_is_shown_beside_the_manifest_actual(
    rec: ModuleType,
) -> None:
    report = rec.reconcile([_row(rec, 1, "a", "1", "1")], [_job("a")], D("7"))
    assert report.lines[0].charge == D("1")
    assert rec.format_report(report).splitlines()[1].endswith(" 1")
