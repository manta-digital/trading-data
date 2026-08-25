"""Drift guard for the Kalshi deploy artifacts (slice 263, Task 6.7).

These files are installed verbatim on manta9000, where nothing type-checks
them: the guard is that the unit's ``ExecStart`` names a command the CLI
actually exposes, that the unit pair carries the settings design 263 chose
deliberately, and that the installer and ``mt-run`` both know about the
pair. No database, no systemd, no network.
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

_REPO_ROOT = Path(__file__).parents[3]
_SYSTEMD = _REPO_ROOT / "deploy" / "systemd"
SERVICE_NAME = "mt-kalshi-pass.service"
TIMER_NAME = "mt-kalshi-pass.timer"
SERVICE = _SYSTEMD / SERVICE_NAME
TIMER = _SYSTEMD / TIMER_NAME
INSTALLER = _REPO_ROOT / "deploy" / "install-production.sh"
MT_RUN = _REPO_ROOT / "deploy" / "mt-run"
ENV_EXAMPLE = _REPO_ROOT / "deploy" / "manta-trading.env.example"

#: The command the unit runs; the CLI drift guard asserts it exists.
PASS_COMMAND = "mt data kalshi pass"


def _parse(path: Path) -> configparser.ConfigParser:
    """systemd allows repeated keys and ``%`` specifiers; ini-parse leniently."""
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # systemd keys are case-sensitive
    parser.read_string(path.read_text(encoding="utf-8"))
    return parser


def _array_block(text: str, name: str) -> str:
    """The body of a ``NAME=( … )`` bash array declaration."""
    match = re.search(rf"^{name}=\((.*?)\)", text, re.DOTALL | re.MULTILINE)
    assert match, f"{name}=( … ) not found"
    return match.group(1)


@pytest.fixture(scope="module")
def service() -> configparser.ConfigParser:
    return _parse(SERVICE)


@pytest.fixture(scope="module")
def timer() -> configparser.ConfigParser:
    return _parse(TIMER)


class TestServiceUnit:
    def test_exists(self):
        assert SERVICE.is_file()

    def test_execstart_runs_the_pass_command(self, service):
        assert service["Service"]["ExecStart"].endswith(PASS_COMMAND)
        # the venv entry point, not `uv run` (deploy-time tool only)
        assert service["Service"]["ExecStart"].startswith(
            "/opt/manta-trading/.venv/bin/mt"
        )

    def test_runs_as_a_bounded_oneshot_in_the_acquisition_slice(self, service):
        assert service["Service"]["Type"] == "oneshot"
        assert service["Service"]["Slice"] == "manta-acquisition.slice"
        assert service["Service"]["User"] == "manta-trading"
        assert service["Service"]["Group"] == "manta-trading"
        assert service["Service"]["WorkingDirectory"] == "/opt/manta-trading"
        assert service["Service"]["EnvironmentFile"] == "/etc/manta-trading.env"

    def test_no_restart_and_no_install_section(self, service):
        """Recovery is the next timer firing; the timer gets enabled, not this."""
        assert "Restart" not in service["Service"]
        assert not service.has_section("Install")

    def test_start_is_unbounded_and_stop_has_no_grace_period(self, service):
        """Decision 5: a catch-up pass runs long; SIGTERM ends it losslessly."""
        assert service["Service"]["TimeoutStartSec"] == "infinity"
        assert "TimeoutStopSec" not in service["Service"]

    def test_hardening_matches_the_eodhd_units(self, service):
        for key in ("NoNewPrivileges", "ProtectHome", "PrivateTmp"):
            assert service["Service"][key] == "true", key
        assert service["Service"]["ProtectSystem"] == "full"


class TestTimerUnit:
    def test_fires_hourly_at_twenty_past_utc(self, timer):
        """Decision 4: the cadence lives here and nowhere else."""
        assert ":20:00 UTC" in timer["Timer"]["OnCalendar"]
        assert timer["Timer"]["Persistent"] == "true"

    def test_names_the_service_and_installs_into_timers_target(self, timer):
        assert timer["Timer"]["Unit"] == SERVICE_NAME
        assert timer["Install"]["WantedBy"] == "timers.target"

    def test_declares_exactly_one_schedule(self, timer):
        schedules = [
            line
            for line in TIMER.read_text().splitlines()
            if line.startswith(("OnCalendar=", "OnUnitActiveSec=", "OnBootSec="))
        ]
        assert schedules == ["OnCalendar=*-*-* *:20:00 UTC"]


class TestInstaller:
    def test_both_units_are_in_the_units_array(self):
        """Not merely somewhere in the file — inside the array it installs."""
        block = _array_block(INSTALLER.read_text(), "UNITS")
        assert SERVICE_NAME in block
        assert TIMER_NAME in block

    def test_cutover_hint_names_the_timer(self):
        assert "enable --now" in INSTALLER.read_text()
        assert TIMER_NAME in INSTALLER.read_text()


class TestMtRun:
    def test_kalshi_is_a_declared_kind(self):
        block = _array_block(MT_RUN.read_text(), "KINDS")
        assert "kalshi" in block.split()

    def test_unit_name_is_derived_from_the_kind(self):
        """`unit_for` builds the name, so a kind cannot name a missing unit."""
        assert 'echo "mt-${1}-pass.service"' in MT_RUN.read_text()


class TestEnvExample:
    def test_kalshi_variables_are_documented_and_commented_out(self):
        lines = ENV_EXAMPLE.read_text().splitlines()
        kalshi = [line for line in lines if "MT_KALSHI" in line]
        assert len(kalshi) == 3
        assert all(line.startswith("#") for line in kalshi)

    def test_private_key_lives_outside_home(self):
        """ProtectHome=true means a PEM under /home is unreadable to the unit."""
        text = ENV_EXAMPLE.read_text()
        line = next(
            line for line in text.splitlines() if "MT_KALSHI_PRIVATE_KEY_PATH" in line
        )
        value = line.split("=", 1)[1].split("#")[0].strip()
        assert value == "/etc/manta-trading-kalshi.pem"
        assert not value.startswith("/home")
        # and the rule itself is stated for whoever fills the real file in
        assert "never under /home" in line


class TestCliDriftGuard:
    def test_the_pass_command_the_unit_runs_exists(self):
        """Criterion 5: ExecStart names a real command."""
        from manta_trading.cli.app import app

        result = CliRunner().invoke(app, ["data", "kalshi", "--help"])
        assert result.exit_code == 0, result.output
        assert "pass" in result.output
