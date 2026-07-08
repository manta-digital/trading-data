"""E2E integration tests — ``--list`` scope drains and exits (slice 146 T28e).

SC3: ``--list NAME`` scope drains and exits 0 when ``--stop-when-done`` is passed.
SC3b: Same list scope exits 0 even without ``--stop-when-done`` (Decision B:
      scoped invocations default to ``terminate_when_drained=True``).

Both scenarios use a temporary YAML config with a 2-symbol list (SPY and AAPL)
and run ``mt data daemon run`` as a real subprocess.

Skipped when MT_TIMESCALE_DB_URL or MT_EODHD_API_KEY is not set.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")
EODHD_KEY = os.environ.get("MT_EODHD_API_KEY", "")

pytestmark = pytest.mark.skipif(
    not (TIMESCALE_URL and EODHD_KEY),
    reason="MT_TIMESCALE_DB_URL and MT_EODHD_API_KEY required",
)

_LIST_NAME = "test-pair"
_LIST_YAML = """\
lists:
  test-pair:
    symbols:
      - SPY
      - AAPL
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_daemon(extra_args: list[str], config_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "manta_trading.cli",
            "data",
            "daemon",
            "run",
            "--list",
            _LIST_NAME,
            "--daily",
            "--no-minute",
            "--config",
            str(config_path),
            *extra_args,
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env=os.environ.copy(),
    )


# ---------------------------------------------------------------------------
# SC3: explicit --stop-when-done drains and exits 0
# ---------------------------------------------------------------------------


def test_sc3_list_scope_drains_with_stop_when_done(tmp_path: Path):
    """Daemon exits 0 when scope drains under --stop-when-done --list."""
    config_file = tmp_path / "symbol-lists.yaml"
    config_file.write_text(_LIST_YAML)

    result = _run_daemon(["--stop-when-done"], config_file)
    assert result.returncode == 0, (
        f"Daemon exited non-zero.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# SC3b: default termination for --list scope (Decision B)
# ---------------------------------------------------------------------------


def test_sc3b_list_scope_drains_by_default(tmp_path: Path):
    """Daemon exits 0 for --list scope without --stop-when-done (default is drain)."""
    config_file = tmp_path / "symbol-lists.yaml"
    config_file.write_text(_LIST_YAML)

    result = _run_daemon([], config_file)
    assert result.returncode == 0, (
        f"Daemon exited non-zero.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
