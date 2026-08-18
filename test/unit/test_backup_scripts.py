"""Refusal behavior of the slice-915 backup scripts (no database required).

Success criterion 8: the tools take their target from explicit arguments and
never from the environment. Each missing-argument case must fail loudly and
name the missing argument; a static check asserts the scripts contain no read
of the production URL variables so a later edge toward ambient configuration
is caught mechanically.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parents[2]
_SCRIPTS = {
    "backup_metadata": _REPO_ROOT / "scripts" / "backup_metadata.sh",
    "backup_prod": _REPO_ROOT / "scripts" / "backup_prod.sh",
    "check_archive_health": _REPO_ROOT / "scripts" / "check_archive_health.sh",
}
# Scripts taking the --db-url/--dest argument pair (check_archive_health has
# no destination, so its refusal cases are separate below).
_DUMP_SCRIPTS = ("backup_metadata", "backup_prod")


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), *args], capture_output=True, text=True, timeout=30
    )


@pytest.mark.parametrize("script_key", _DUMP_SCRIPTS)
class TestArgumentRefusal:
    def test_no_arguments_refused(self, script_key: str) -> None:
        result = _run(_SCRIPTS[script_key])
        assert result.returncode != 0
        assert "--db-url" in result.stderr

    def test_dest_alone_refused(self, script_key: str) -> None:
        result = _run(_SCRIPTS[script_key], "--dest", "/nonexistent/dest")
        assert result.returncode != 0
        assert "--db-url" in result.stderr

    def test_db_url_alone_refused(self, script_key: str) -> None:
        result = _run(
            _SCRIPTS[script_key], "--db-url", "postgresql://localhost/nowhere"
        )
        assert result.returncode != 0
        assert "--dest" in result.stderr

    def test_unknown_argument_refused(self, script_key: str) -> None:
        result = _run(_SCRIPTS[script_key], "--frobnicate")
        assert result.returncode != 0
        assert "unknown argument" in result.stderr


class TestArchiveHealthRefusal:
    def test_no_arguments_refused(self) -> None:
        result = _run(_SCRIPTS["check_archive_health"])
        assert result.returncode != 0
        assert "--db-url" in result.stderr

    def test_unknown_argument_refused(self) -> None:
        result = _run(_SCRIPTS["check_archive_health"], "--frobnicate")
        assert result.returncode != 0
        assert "unknown argument" in result.stderr


@pytest.mark.parametrize("script_key", sorted(_SCRIPTS))
def test_no_ambient_database_url(script_key: str) -> None:
    """The scripts must not name any MT_* URL variable at all.

    Stronger than the Python-tier ratchet (which matches env-read syntax): a
    shell script has too many read spellings (``$VAR``, ``${VAR}``,
    ``printenv``), so the absence of the variable *names* is the property that
    holds them all off.
    """
    text = _SCRIPTS[script_key].read_text(encoding="utf-8")
    for needle in (
        "MT_TIMESCALE" + "_DB_URL",
        "MT_TIMESCALE" + "_MAINTENANCE_URL",
        "MT_TIMESCALE" + "_TEST_URL",
    ):
        assert needle not in text, f"{script_key} references {needle}"


@pytest.mark.parametrize("script_key", sorted(_SCRIPTS))
def test_script_is_executable(script_key: str) -> None:
    script = _SCRIPTS[script_key]
    assert script.exists()
    assert script.stat().st_mode & 0o111, f"{script.name} is not executable"
