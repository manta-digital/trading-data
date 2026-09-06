"""Tests for ``scripts/wal_segment_name.py`` (slice 920, Task 1.2).

Expected values are literals worked out by hand from the segment-name layout
(8 hex timeline, 8 hex log file, 8 hex segment; 256 segments per log file),
not derived from the code under test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "wal_segment_name.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_SCRIPT), *args], capture_output=True, text=True, timeout=30
    )


class TestNext:
    def test_increments_segment(self) -> None:
        result = _run("next", "000000010000121700000083")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "000000010000121700000084"

    def test_rolls_over_to_next_log_file(self) -> None:
        result = _run("next", "0000000100001217000000FF")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "000000010000121800000000"

    def test_preserves_timeline(self) -> None:
        result = _run("next", "0000000200001217000000FF")
        assert result.stdout.strip() == "000000020000121800000000"

    def test_log_file_rollover_carries_into_high_digits(self) -> None:
        result = _run("next", "00000001000012FF000000FF")
        assert result.stdout.strip() == "000000010000130000000000"

    def test_output_is_upper_case_and_zero_padded(self) -> None:
        result = _run("next", "00000001000000000000000a")
        assert result.stdout.strip() == "00000001000000000000000B"


class TestFromLsn:
    def test_segment_holding_lsn(self) -> None:
        result = _run("from-lsn", "1", "1217/83A00000")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "000000010000121700000083"

    def test_segment_boundary_belongs_to_that_segment(self) -> None:
        # 0x74000000 is exactly 0x74 * 16 MiB: the first byte of segment 74.
        result = _run("from-lsn", "1", "11A6/74000000")
        assert result.stdout.strip() == "00000001000011A600000074"

    def test_last_byte_of_segment(self) -> None:
        result = _run("from-lsn", "1", "11A6/73FFFFFF")
        assert result.stdout.strip() == "00000001000011A600000073"

    def test_timeline_is_decimal(self) -> None:
        result = _run("from-lsn", "12", "0/1000000")
        assert result.stdout.strip() == "0000000C0000000000000001"


class TestRefusal:
    @pytest.mark.parametrize(
        "name",
        [
            "00000001000012170000008",  # 23 chars
            "0000000100001217000000830",  # 25 chars
            "00000001000012170000008G",  # non-hex
            "",
        ],
    )
    def test_malformed_segment_name_exits_2(self, name: str) -> None:
        result = _run("next", name)
        assert result.returncode == 2
        assert result.stdout == ""
        assert "error" in result.stderr

    @pytest.mark.parametrize(
        ("tli", "lsn"),
        [("1", "1217"), ("1", "1217/ZZ"), ("x", "1217/83A00000"), ("1", "1/100000000")],
    )
    def test_malformed_lsn_exits_2(self, tli: str, lsn: str) -> None:
        result = _run("from-lsn", tli, lsn)
        assert result.returncode == 2
        assert result.stdout == ""

    def test_unknown_subcommand_exits_2(self) -> None:
        assert _run("previous", "000000010000121700000083").returncode == 2

    def test_no_arguments_exits_2(self) -> None:
        assert _run().returncode == 2
