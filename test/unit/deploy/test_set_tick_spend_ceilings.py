"""``deploy/set-tick-spend-ceilings.sh`` (slice 304 D4), run against a temp file."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[3] / "deploy" / "set-tick-spend-ceilings.sh"
ORIGINAL = (
    "MT_TIMESCALE_DB_URL=prod\n"
    "MT_TICK_SPEND_CEILING_USD=2.5\n"
    "MT_DATABENTO_API_KEY=secret-key-value\n"
    "MT_TICK_SPEND_30D_CEILING_USD=10\n"
)


def _run(target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"], "MT_CEILINGS_ENV_TARGET": str(target)}
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def target(tmp_path: Path) -> Path:
    path = tmp_path / "target.env"
    path.write_text(ORIGINAL)
    path.chmod(0o640)
    return path


def test_replaces_both_values_in_place_and_prints_old_and_new(target: Path) -> None:
    result = _run(target, "--per-pass", "7", "--cap-30d", "16.5")
    assert result.returncode == 0, result.stderr
    assert target.read_text().splitlines() == [
        "MT_TIMESCALE_DB_URL=prod",
        "MT_TICK_SPEND_CEILING_USD=7",
        "MT_DATABENTO_API_KEY=secret-key-value",
        "MT_TICK_SPEND_30D_CEILING_USD=16.5",
    ]
    assert target.stat().st_mode & 0o777 == 0o640
    assert "MT_TICK_SPEND_CEILING_USD: 2.5 -> 7" in result.stdout
    assert "MT_TICK_SPEND_30D_CEILING_USD: 10 -> 16.5" in result.stdout
    assert "secret-key-value" not in result.stdout + result.stderr


@pytest.mark.parametrize("bad", ["abc", "-1", "1e3", ""])
def test_a_bad_value_is_refused_and_the_file_is_untouched(
    target: Path, bad: str
) -> None:
    result = _run(target, "--per-pass", bad, "--cap-30d", "16")
    assert result.returncode != 0
    assert target.read_text() == ORIGINAL


def test_a_missing_key_is_refused_and_named(target: Path) -> None:
    target.write_text("MT_TICK_SPEND_CEILING_USD=2.5\n")
    result = _run(target, "--per-pass", "7", "--cap-30d", "16")
    assert result.returncode != 0
    assert "MT_TICK_SPEND_30D_CEILING_USD" in result.stderr
    assert target.read_text() == "MT_TICK_SPEND_CEILING_USD=2.5\n"
