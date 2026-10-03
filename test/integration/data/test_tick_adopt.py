"""Adoption end to end (slice 223; LLD 224 TD10, Functional Requirement 2).

A migrated tick database, the real ``CME_EQUITY`` calendar from
``session_migrated_db``, the real adapter over a fake SDK client, and a job
directory of day files built from the committed fixtures.

The job spans Friday 2024-09-06 → Tuesday 2024-09-10 (end exclusive): its
session days are 09-06, 09-08 (Sunday open) and 09-09. Files exist for 09-06
and 09-08, so 09-09 is a hole; a file for Saturday 09-07 is a stray.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from psycopg_pool import PoolTimeout
from tick_support.batch_responses import batch_api, job_record
from tick_support.dbn_files import bad_header_bytes, day_file_bytes, write_job_dir
from tick_support.fake_historical import FakeApi, FakeHistorical, server_error

from manta_trading.config import Settings
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick import tick_calendar
from manta_trading.data.tick.adopt import (
    AdoptResult,
    TickVerifyInterrupted,
    adopt_job,
)
from manta_trading.data.tick.adopt_files import TickAdoptionRefused
from manta_trading.data.tick.constants import (
    CME_DATASET,
    TICK_ENV_PREFIX,
    SType,
    UnitState,
)
from manta_trading.data.tick.databento.adapter import DatabentoTickProvider
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.run_context import open_tick_run
from manta_trading.data.tick.tick_calendar import TickCalendarError

JOB = "GLBX-20240910-ADOPTTEST"
RECORDS_PER_DAY = 3
FILE_DAYS = (date(2024, 9, 6), date(2024, 9, 8))
STRAY_DAY = date(2024, 9, 7)
TS_RECEIVED = "2024-09-10 05:17:15.813852+00:00"


def _file_name(day: date) -> str:
    return f"glbx-mdp3-{day:%Y%m%d}.trades.dbn.zst"


@pytest.fixture(autouse=True)
def no_tick_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.upper().startswith(TICK_ENV_PREFIX):
            monkeypatch.delenv(name)


def _job(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "job_id": JOB,
        "start": "2024-09-06 00:00:00+00:00",
        "end": "2024-09-10 00:00:00+00:00",
        "symbols": "ES.FUT",
        "stype_in": "parent",
        "cost_usd": 12.5785,
        "record_count": 10_049_172,
        "ts_received": TS_RECEIVED,
        "state": "expired",
    }
    return job_record(**(fields | overrides))


@pytest.fixture
def source(tmp_path: Path) -> Path:
    files = {
        _file_name(day): day_file_bytes(
            "test_data.trades.v3.dbn.zst", CME_DATASET, day, SType.PARENT
        )
        for day in (*FILE_DAYS, STRAY_DAY)
    }
    return write_job_dir(tmp_path / "source", JOB, files)


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    return root


RunFactory = Callable[..., Any]


@pytest.fixture
def make_run(archive: Path, session_migrated_db: str) -> RunFactory:
    """``make_run(tick_url, job=..., calendar_url=..., record_count=...)``."""

    def factory(
        tick_url: str,
        job: dict[str, Any] | None = None,
        calendar_url: str = "",
        record_count: object = RECORDS_PER_DAY,
    ) -> Any:
        record = job if job is not None else _job()
        historical = FakeHistorical(
            metadata=FakeApi({"get_record_count": record_count}),
            batch=batch_api({JOB: record}),
        )
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            databento_api_key="db-test-key",
            tick_db_url=tick_url,
            tick_archive_dir=archive,
            timescale_db_url=calendar_url or session_migrated_db,
        )
        return open_tick_run(
            settings,
            provider_factory=lambda _: DatabentoTickProvider(
                historical,  # type: ignore[arg-type]
                httpx.Client(),
            ),
            env_file=None,
        )

    return factory


async def _adopt(
    make_run: RunFactory, tick_url: str, source: Path, **kw: Any
) -> AdoptResult:
    async with make_run(tick_url, **kw) as run:
        return await adopt_job(run, JOB, source, DbnFileReader())


def _rows(url: str, query: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(url) as conn:
        return conn.execute(query).fetchall()  # type: ignore[arg-type]


_REQUEST_QUERY = (
    "SELECT provider_job_id, is_adopted, estimated_cost_usd, actual_cost_usd,"
    " requested_at, committed_at, download_deadline, provider_record_count,"
    " symbols, stype_in, range_start, range_end FROM tick_request"
)
_UNIT_QUERY = (
    "SELECT unit_date, state, fetch_status, file_path, file_size_bytes,"
    " file_sha256, provider_record_count FROM tick_archive_unit ORDER BY unit_date"
)


async def test_adoption_records_verified_units_holes_and_strays(
    make_run: RunFactory, migrated_tick_db: str, source: Path
) -> None:
    result = await _adopt(make_run, migrated_tick_db, source)
    assert result.cost_usd == Decimal("12.5785")
    assert result.units_by_state == {
        UnitState.VERIFIED.value: 2,
        UnitState.DELIVERED.value: 1,
    }
    assert result.holes == (date(2024, 9, 9),)
    assert result.strays == (_file_name(STRAY_DAY),)
    assert result.verify_failures == ()
    [request] = _rows(migrated_tick_db, _REQUEST_QUERY)
    received = datetime(2024, 9, 10, 5, 17, 15, 813852, tzinfo=UTC)
    assert request == (
        JOB, True, Decimal("12.5785"), Decimal("12.5785"), received, received,
        None, 10_049_172, ["ES.FUT"], SType.PARENT.value,
        date(2024, 9, 6), date(2024, 9, 10),
    )  # fmt: skip
    units = _rows(migrated_tick_db, _UNIT_QUERY)
    verified = (UnitState.VERIFIED.value, FetchStatus.UNKNOWN.value, RECORDS_PER_DAY)
    hole = (UnitState.DELIVERED.value, FetchStatus.PROVIDER_HOLE.value, None)
    assert [(u[0], u[1], u[2], u[6]) for u in units] == [
        (FILE_DAYS[0], *verified),
        (FILE_DAYS[1], *verified),
        (date(2024, 9, 9), *hole),
    ]
    assert units[0][3] == f"{JOB}/{_file_name(FILE_DAYS[0])}"


async def test_second_adoption_writes_nothing(
    make_run: RunFactory, migrated_tick_db: str, source: Path
) -> None:
    await _adopt(make_run, migrated_tick_db, source)
    before = _rows(migrated_tick_db, _UNIT_QUERY)
    again = await _adopt(make_run, migrated_tick_db, source)
    assert again.already_adopted
    assert _rows(migrated_tick_db, _UNIT_QUERY) == before


def _fails_after_first() -> Callable[[dict[str, Any]], int]:
    """A record count that answers once, then the provider goes down."""
    calls = itertools.count(1)

    def answer(kwargs: dict[str, Any]) -> int:
        if next(calls) > 1:
            raise server_error(503)
        return RECORDS_PER_DAY

    return answer


async def test_readoption_resumes_an_interrupted_verification(
    make_run: RunFactory, migrated_tick_db: str, source: Path
) -> None:
    with pytest.raises(TickVerifyInterrupted, match="1 unit\\(s\\) left unverified"):
        await _adopt(
            make_run, migrated_tick_db, source, record_count=_fails_after_first()
        )
    states = [u[1] for u in _rows(migrated_tick_db, _UNIT_QUERY)]
    assert states.count(UnitState.DOWNLOADED.value) == 1
    again = await _adopt(make_run, migrated_tick_db, source)
    assert (again.already_adopted, again.reverified, again.verify_failures) == (
        True,
        1,
        (),
    )
    states = [u[1] for u in _rows(migrated_tick_db, _UNIT_QUERY)]
    assert states.count(UnitState.VERIFIED.value) == 2


async def test_rebuild_from_the_archive_gives_identical_rows(
    make_run: RunFactory,
    migrated_tick_db: str,
    second_migrated_tick_db: str,
    source: Path,
    archive: Path,
) -> None:
    await _adopt(make_run, migrated_tick_db, source)
    rebuilt = await _adopt(make_run, second_migrated_tick_db, archive / JOB)
    assert not any(f.copied for f in rebuilt.files)
    for query in (_REQUEST_QUERY, _UNIT_QUERY):
        assert _rows(second_migrated_tick_db, query) == _rows(migrated_tick_db, query)


def _flip_last_byte(path: Path) -> None:
    content = bytearray(path.read_bytes())
    content[-1] ^= 0xFF
    path.write_bytes(bytes(content))


async def test_corrupted_file_refuses_with_no_rows(
    make_run: RunFactory, migrated_tick_db: str, source: Path
) -> None:
    _flip_last_byte(source / _file_name(FILE_DAYS[1]))
    with pytest.raises(TickAdoptionRefused, match=_file_name(FILE_DAYS[1])):
        await _adopt(make_run, migrated_tick_db, source)
    assert _rows(migrated_tick_db, "SELECT 1 FROM tick_request") == []


async def test_queued_job_is_refused_naming_the_state(
    make_run: RunFactory, migrated_tick_db: str, source: Path
) -> None:
    with pytest.raises(TickAdoptionRefused, match="is queued"):
        await _adopt(make_run, migrated_tick_db, source, job=_job(state="queued"))
    assert _rows(migrated_tick_db, "SELECT 1 FROM tick_request") == []


async def test_unreachable_calendar_raises_with_no_rows(
    make_run: RunFactory,
    migrated_tick_db: str,
    source: Path,
    archive: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The calendar pool's timeout (a ``psycopg.OperationalError``), raised at once."""

    def unreachable(*args: Any) -> list[date]:
        raise PoolTimeout("couldn't get a connection after 30.00 sec")

    monkeypatch.setattr(tick_calendar, "session_days", unreachable)
    with pytest.raises(TickCalendarError, match="CME_EQUITY unavailable"):
        await _adopt(make_run, migrated_tick_db, source)
    assert _rows(migrated_tick_db, "SELECT 1 FROM tick_request") == []
    assert not (archive / JOB).exists()


async def test_range_outside_the_calendar_raises_with_no_rows(
    make_run: RunFactory, migrated_tick_db: str, source: Path, archive: Path
) -> None:
    early = _job(start="2019-12-02 00:00:00+00:00", end="2019-12-04 00:00:00+00:00")
    with pytest.raises(TickCalendarError, match="CME_EQUITY"):
        await _adopt(make_run, migrated_tick_db, source, job=early)
    assert _rows(migrated_tick_db, "SELECT 1 FROM tick_request") == []
    assert not (archive / JOB).exists()


async def test_a_refused_header_fails_the_unclaimed_days_not_the_adoption(
    make_run: RunFactory, migrated_tick_db: str, tmp_path: Path
) -> None:
    """09-08's header is refused: it cannot name its day, so 09-08 and 09-09
    (unclaimed) fail naming it instead of being called holes (226 TD8)."""
    good, bad = FILE_DAYS
    fixture = "test_data.trades.v3.dbn.zst"
    files = {
        _file_name(good): day_file_bytes(fixture, CME_DATASET, good, SType.PARENT),
        _file_name(bad): bad_header_bytes(
            day_file_bytes(fixture, CME_DATASET, bad, SType.PARENT), "stype_out"
        ),
    }
    source = write_job_dir(tmp_path / "bad-source", JOB, files)
    result = await _adopt(make_run, migrated_tick_db, source)
    assert result.holes == ()
    assert result.units_by_state == {
        UnitState.VERIFIED.value: 1,
        UnitState.DELIVERED.value: 2,
    }
    assert any(
        line.startswith("header: ") and _file_name(bad) in line
        for line in result.verify_failures
    )
    failed = _rows(
        migrated_tick_db,
        "SELECT unit_date, fetch_status, failure_reason FROM tick_archive_unit"
        " WHERE state = 'delivered' ORDER BY unit_date",
    )
    assert [(day, status) for day, status, _ in failed] == [
        (bad, FetchStatus.RETRY_EXHAUSTED.value),
        (date(2024, 9, 9), FetchStatus.RETRY_EXHAUSTED.value),
    ]
    assert all(reason.startswith("header: no readable file") for *_, reason in failed)
