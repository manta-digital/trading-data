"""``MemoryMax`` derivation for ``manta-acquisition.slice`` (slice 233, TD 2)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import acquisition_memory_bound as amb  # noqa: E402

MIB = 1024**2

# Verbatim from manta9000's journal (233 findings note), except where marked.
MINUTE_G_LINE = (
    "mt-minute-pass.service: Consumed 4min 58.190s CPU time over 1h 28min "
    "37.217s wall clock time, 6.2G memory peak."
)
DAILY_M_LINE = (
    "mt-daily-pass.service: Consumed 3.093s CPU time over 999ms wall clock "
    "time, 90M memory peak."
)
KALSHI_FRACTIONAL_M_LINE = (
    "mt-kalshi-pass.service: Consumed 1min 10.213s CPU time over 2min 33.383s "
    "wall clock time, 254.7M memory peak."
)
# Synthetic: no line in the 30-day window lacks the clause.
NO_PEAK_LINE = (
    "mt-daily-pass.service: Consumed 3.093s CPU time over 999ms wall clock time."
)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (MINUTE_G_LINE, int(6.2 * 1024**3)),
        (DAILY_M_LINE, 90 * MIB),
        (KALSHI_FRACTIONAL_M_LINE, int(254.7 * MIB)),
        (NO_PEAK_LINE, None),
        ("Starting mt-daily-pass.service - daily pass...", None),
    ],
)
def test_parse_peak_on_journal_lines(line: str, expected: int | None) -> None:
    assert amb.parse_peak(line) == expected


def test_parse_peak_finds_clause_with_trailing_swap_peak() -> None:
    line = "x.service: Consumed 2s CPU time, 1.5G memory peak, 0B memory swap peak."
    assert amb.parse_peak(line) == int(1.5 * 1024**3)


def test_max_peak_takes_largest() -> None:
    lines = [DAILY_M_LINE, KALSHI_FRACTIONAL_M_LINE, NO_PEAK_LINE]
    assert amb.max_peak(lines, "u.service") == int(254.7 * MIB)


@pytest.mark.parametrize("lines", [[], [NO_PEAK_LINE, "-- No entries --"]])
def test_max_peak_raises_without_a_peak(lines: list[str]) -> None:
    with pytest.raises(amb.MemoryBoundError, match="u.service"):
        amb.max_peak(lines, "u.service")


def test_memory_max_on_design_inputs() -> None:
    peaks = {"m": int(6.2 * 1024**3), "d": 426 * MIB, "k": 317 * MIB}
    assert amb.memory_max_gib(peaks) == 14


def test_memory_max_does_not_round_up_an_exact_gib() -> None:
    assert amb.memory_max_gib({"a": 3 * amb.GIB, "b": 2 * amb.GIB}) == 10


def test_memory_max_raises_on_no_members() -> None:
    with pytest.raises(amb.MemoryBoundError):
        amb.memory_max_gib({})


def test_members_from_real_unit_files() -> None:
    found = set(amb.members())
    # Not an exact set: 234 adds its tick unit.
    assert {
        "mt-minute-pass.service",
        "mt-daily-pass.service",
        "mt-kalshi-pass.service",
    } <= found


def test_members_excludes_other_slices(tmp_path: Path) -> None:
    (tmp_path / "in.service").write_text(
        "[Service]\nType=oneshot\n  Slice = manta-acquisition.slice \n"
    )
    (tmp_path / "none.service").write_text("[Service]\nType=oneshot\n")
    (tmp_path / "other.service").write_text("[Service]\nSlice=system.slice\n")
    assert amb.members(tmp_path) == ["in.service"]


CANNED = {
    "mt-minute-pass.service": [MINUTE_G_LINE],
    "mt-daily-pass.service": [DAILY_M_LINE],
    "mt-kalshi-pass.service": [KALSHI_FRACTIONAL_M_LINE],
}


@pytest.fixture
def canned_journal(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    journal = dict(CANNED)
    monkeypatch.setattr(amb, "members", lambda: sorted(journal))
    monkeypatch.setattr(amb, "read_journal", lambda unit: journal[unit])
    return journal


def test_main_prints_members_and_result(
    canned_journal: dict[str, list[str]],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert amb.main() == 0
    out = capsys.readouterr().out
    for unit in CANNED:
        assert unit in out
    assert "MemoryMax=14G" in out
    assert list(tmp_path.iterdir()) == []


def test_main_exits_2_naming_a_member_without_a_peak(
    canned_journal: dict[str, list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    canned_journal["mt-daily-pass.service"] = [NO_PEAK_LINE]
    assert amb.main() == 2
    assert "mt-daily-pass.service" in capsys.readouterr().err


def test_main_exits_2_on_a_failed_journal_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    failed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="Failed to open journal"
    )
    monkeypatch.setattr(amb.subprocess, "run", lambda *a, **k: failed)
    assert amb.main() == 2
    err = capsys.readouterr().err
    assert "Failed to open journal" in err
    assert ".service" in err
