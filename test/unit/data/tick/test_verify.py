"""Unit verification over day files built from the fixtures (223; LLD 224 TD9).

The manifest writes are patched with recorders (the transitions themselves
are integration-tested); the provider is the real adapter over a fake SDK.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from tick_support.dbn_files import write_day_file
from tick_support.fake_historical import FakeApi, FakeHistorical, server_error

from manta_trading.config import Settings
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick import verify
from manta_trading.data.tick.constants import (
    CME_DATASET,
    SType,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.databento.adapter import DatabentoTickProvider
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.manifest_reads import UnitFile, UnitRow
from manta_trading.data.tick.run_context import TickRun
from manta_trading.providers.errors import ProviderError

DAY = date(2024, 9, 3)
NOW = datetime(2026, 9, 28, tzinfo=UTC)
RECORDS = 511_965
REL_PATH = "GLBX-TEST/glbx-mdp3-20240903.trades.dbn.zst"


class Recorder:
    """Stands in for ``mark_verified`` and ``record_failure``."""

    def __init__(self) -> None:
        self.verified: list[tuple[int, int]] = []
        self.failures: list[tuple[int, str, bool]] = []

    async def mark_verified(
        self, conn: Any, unit_id: int, count: int, now: Any
    ) -> None:
        self.verified.append((unit_id, count))

    async def record_failure(
        self, conn: Any, unit_id: int, state: Any, reason: str, now: Any, **kw: bool
    ) -> None:
        self.failures.append((unit_id, reason, kw["deterministic"]))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(verify, "mark_verified", rec.mark_verified)
    monkeypatch.setattr(verify, "record_failure", rec.record_failure)
    return rec


@pytest.fixture
def metadata() -> FakeApi:
    return FakeApi({"get_record_count": RECORDS})


@pytest.fixture
def run(tmp_path: Path, metadata: FakeApi) -> TickRun:
    provider = DatabentoTickProvider(
        FakeHistorical(metadata=metadata),  # type: ignore[arg-type]
        httpx.Client(),
    )
    return TickRun(
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        provider=provider,
        conn=None,  # type: ignore[arg-type]
        archive_root=tmp_path,
        run_id="test",
        clock=lambda: NOW,
    )


def _unit(run: TickRun, day: date = DAY, **header: Any) -> UnitRow:
    """A downloaded unit whose file is a fixture re-headed per ``header``."""
    dest = run.archive_root / REL_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = write_day_file(
        dest,
        header.get("fixture", "test_data.trades.v3.dbn.zst"),
        header.get("dataset", CME_DATASET),
        day,
        SType.PARENT,
    )
    return UnitRow(
        unit_id=7,
        request_id=1,
        unit_date=DAY,
        state=UnitState.DOWNLOADED,
        fetch_status=FetchStatus.UNKNOWN,
        failure_reason=None,
        attempt_count=0,
        reopened_at=None,
        file=UnitFile(REL_PATH, written.size, written.sha256),
        provider_record_count=None,
        dataset=CME_DATASET,
        schema=TickSchema.TRADES,
        symbols=("ES.FUT",),
        stype_in=SType.PARENT,
    )


async def test_matching_file_verifies_with_the_provider_count(
    run: TickRun, recorder: Recorder, metadata: FakeApi
) -> None:
    outcome = await verify.check(run, _unit(run), DbnFileReader())
    assert outcome == verify.VerifyOutcome(7, None)
    assert recorder.verified == [(7, RECORDS)]
    [call] = metadata.calls_to("get_record_count")
    assert call["symbols"] == ["ES.FUT"]
    assert (call["start"], call["end"]) == (
        datetime(2024, 9, 3, tzinfo=UTC),
        datetime(2024, 9, 4, tzinfo=UTC),
    )


def _flip_one_byte(path: Path) -> None:
    content = bytearray(path.read_bytes())
    content[-1] ^= 0xFF
    path.write_bytes(bytes(content))


async def test_flipped_byte_fails_on_the_hash(run: TickRun, recorder: Recorder) -> None:
    unit = _unit(run)
    _flip_one_byte(run.archive_root / REL_PATH)
    outcome = await verify.check(run, unit, DbnFileReader())
    assert outcome.failure == "file SHA-256 differs from the recorded hash"
    assert recorder.failures == [(7, outcome.failure, True)]
    assert recorder.verified == []


async def test_wrong_size_fails_naming_it(run: TickRun, recorder: Recorder) -> None:
    unit = _unit(run)
    assert unit.file is not None
    wrong = replace(unit, file=replace(unit.file, size=unit.file.size + 1))
    outcome = await verify.check(run, wrong, DbnFileReader())
    assert outcome.failure is not None and "file size" in outcome.failure


async def test_missing_file_fails(run: TickRun, recorder: Recorder) -> None:
    unit = _unit(run)
    (run.archive_root / REL_PATH).unlink()
    outcome = await verify.check(run, unit, DbnFileReader())
    assert outcome.failure == f"file {REL_PATH} is missing"


@pytest.mark.parametrize(
    ("header", "field"),
    [
        ({"day": date(2024, 9, 4)}, "header start"),
        ({"fixture": "test_data.tbbo.v3.dbn.zst"}, "header schema"),
        ({"dataset": "XNAS.ITCH"}, "header dataset"),
    ],
)
async def test_header_mismatch_names_the_field(
    run: TickRun, recorder: Recorder, header: dict[str, Any], field: str
) -> None:
    day = header.pop("day", DAY)
    outcome = await verify.check(run, _unit(run, day, **header), DbnFileReader())
    assert outcome.failure is not None
    assert outcome.failure.startswith(field)
    assert recorder.failures == [(7, outcome.failure, True)]


async def test_wrong_stype_names_the_field(run: TickRun, recorder: Recorder) -> None:
    unit = replace(_unit(run), stype_in=SType.CONTINUOUS)
    outcome = await verify.check(run, unit, DbnFileReader())
    assert outcome.failure is not None and outcome.failure.startswith("header stype_in")


async def test_record_count_failure_raises(
    run: TickRun, recorder: Recorder, metadata: FakeApi
) -> None:
    metadata.fail("get_record_count", server_error())
    with pytest.raises(ProviderError):
        await verify.check(run, _unit(run), DbnFileReader())
    assert recorder.verified == [] and recorder.failures == []
