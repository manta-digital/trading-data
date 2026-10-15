"""``deploy/add-tick-env.sh`` (slice 234 TD3), run against temp files.

The root-only steps are reached through the script's test hooks: the target,
owner and log directory are temp values, and the chown/chmod run behind an
``echo`` prefix so the test sees them issued without root.
"""

from __future__ import annotations

import getpass
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[3] / "deploy" / "add-tick-env.sh"
KEYS = (
    "MT_TICK_DB_URL",
    "MT_DATABENTO_API_KEY",
    "MT_TICK_SPEND_CEILING_USD",
    "MT_TICK_SPEND_30D_CEILING_USD",
    "MT_TICK_ARCHIVE_DIR",
)
#: Recognisable values, so a test can prove none of them reaches any output.
VALUES = {key: f"secret-{index}-value" for index, key in enumerate(KEYS)}
MAINTENANCE = "MT_TICK_MAINTENANCE_URL=postgresql://maint:secret-maint@h/db"


def _source(tmp_path: Path, *, drop: str | None = None) -> Path:
    lines = [f"{k}={v}" for k, v in VALUES.items() if k != drop]
    source = tmp_path / "checkout.env"
    source.write_text("\n".join([*lines, MAINTENANCE, "OTHER=x"]) + "\n")
    return source


def _run(tmp_path: Path, source: Path) -> subprocess.CompletedProcess[str]:
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ["PATH"],
        "MT_ADD_TICK_ENV_TARGET": str(tmp_path / "target.env"),
        "MT_ADD_TICK_ENV_OWNER": getpass.getuser(),
        "MT_ADD_TICK_ENV_GROUP": "manta-trading",
        "MT_ADD_TICK_ENV_LOG_DIR": str(logs),
        "MT_ADD_TICK_ENV_PRIV_PREFIX": "echo",
    }
    return subprocess.run(
        ["bash", str(SCRIPT), "--from", str(source)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def target(tmp_path: Path) -> Path:
    path = tmp_path / "target.env"
    path.write_text("MT_TIMESCALE_DB_URL=prod\nMT_TICK_ARCHIVE_DIR=/data/kept\n")
    return path


def _all_output(tmp_path: Path, result: subprocess.CompletedProcess[str]) -> str:
    logs = "".join(p.read_text() for p in (tmp_path / "logs").glob("*.log"))
    return result.stdout + result.stderr + logs


def test_adds_absent_keys_only_and_keeps_a_present_one(
    tmp_path: Path, target: Path
) -> None:
    result = _run(tmp_path, _source(tmp_path))
    assert result.returncode == 0, result.stderr
    lines = target.read_text().splitlines()
    assert lines[:2] == ["MT_TIMESCALE_DB_URL=prod", "MT_TICK_ARCHIVE_DIR=/data/kept"]
    added = {k: v for k, v in VALUES.items() if k != "MT_TICK_ARCHIVE_DIR"}
    assert lines[2:] == [f"{k}={v}" for k, v in added.items()]
    assert "4 keys added" in result.stdout


def test_the_maintenance_credential_is_never_copied(
    tmp_path: Path, target: Path
) -> None:
    _run(tmp_path, _source(tmp_path))
    assert "MT_TICK_MAINTENANCE_URL" not in target.read_text()
    assert "OTHER" not in target.read_text()


def test_a_missing_key_is_refused_and_nothing_is_written(
    tmp_path: Path, target: Path
) -> None:
    before = target.read_text()
    result = _run(tmp_path, _source(tmp_path, drop="MT_DATABENTO_API_KEY"))
    assert result.returncode != 0
    assert "MT_DATABENTO_API_KEY" in _all_output(tmp_path, result)
    assert target.read_text() == before


def test_no_value_appears_in_any_output(tmp_path: Path, target: Path) -> None:
    result = _run(tmp_path, _source(tmp_path))
    output = _all_output(tmp_path, result)
    for value in [*VALUES.values(), "secret-maint"]:
        assert value not in output


def test_a_second_run_adds_nothing(tmp_path: Path, target: Path) -> None:
    _run(tmp_path, _source(tmp_path))
    after_first = target.read_text()
    again = _run(tmp_path, _source(tmp_path))
    assert again.returncode == 0, again.stderr
    assert "0 keys added" in again.stdout
    assert target.read_text() == after_first


def test_mode_and_group_are_reasserted(tmp_path: Path, target: Path) -> None:
    result = _run(tmp_path, _source(tmp_path))
    user = getpass.getuser()
    assert f"chown {user}:manta-trading {target}" in result.stdout
    assert f"chmod 0640 {target}" in result.stdout


def test_the_key_list_is_the_five_and_never_the_maintenance_url() -> None:
    text = SCRIPT.read_text()
    block = text.split("KEYS=(", 1)[1].split(")", 1)[0].split()
    assert tuple(block) == KEYS
