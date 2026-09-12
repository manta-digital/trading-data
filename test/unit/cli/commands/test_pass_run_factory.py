"""Unit tests: building a recorder must never delay the pass (slice 922).

This file exists because of a specific defect. The obvious spelling of the
short-lived-command factory built a ``ConnectionPool`` that filled eagerly,
so every ``mt data health`` and ``mt data kalshi pass`` invocation blocked for
the pool's own thirty-second timeout whenever the database host did not
resolve — and then, once the pool was made lazy instead, wrote an ERROR
traceback to stdout that corrupted the ``--json`` payload.

Recording a pass is bookkeeping. It must cost the pass nothing when the
database is absent: no delay, no stdout, no exception.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from manta_trading.cli.commands._pass_run import (
    make_pass_run_recorder,
    pass_run_recorder,
)
from manta_trading.constants import PASS_RUN_DB_CONNECT_TIMEOUT_SECONDS
from manta_trading.data.acquisition.pass_runs import PassKind

# An address that fails to resolve, standing in for a database that is not
# there. Well under any plausible connect attempt, so a test that takes
# longer than this is measuring a real block, not a slow machine.
_UNRESOLVABLE = "postgresql://nonexistent-host.invalid/db"
_NO_DELAY_SECONDS = 5.0


def _settings(url: str | None) -> MagicMock:
    settings = MagicMock()
    settings.timescale_db_url = url
    return settings


class TestNoDatabaseUrl:
    def test_the_context_manager_yields_none(self) -> None:
        with pass_run_recorder(_settings(None)) as recorder:
            assert recorder is None

    def test_the_daemon_factory_returns_none(self) -> None:
        assert make_pass_run_recorder(_settings(None)) is None


class TestBuildingCostsNothing:
    """Construction must not connect: the first write pays that cost."""

    def test_the_context_manager_returns_immediately(self) -> None:
        started = time.monotonic()
        with pass_run_recorder(_settings(_UNRESOLVABLE)) as recorder:
            assert recorder is not None
        assert time.monotonic() - started < _NO_DELAY_SECONDS

    def test_the_daemon_factory_returns_immediately(self) -> None:
        started = time.monotonic()
        recorder = make_pass_run_recorder(_settings(_UNRESOLVABLE))
        assert recorder is not None
        assert time.monotonic() - started < _NO_DELAY_SECONDS


class TestAnUnreachableDatabaseNeverRaises:
    """The recorder's contract, exercised against a real failing connection."""

    def test_open_returns_none_rather_than_raising(self, caplog) -> None:
        with pass_run_recorder(_settings(_UNRESOLVABLE)) as recorder:
            assert recorder is not None
            assert recorder.open(PassKind.HEALTH) is None

    def test_progress_and_close_on_a_none_run_id_do_nothing(self) -> None:
        from manta_trading.data.acquisition.pass_runs import PassRunOutcome

        with pass_run_recorder(_settings(_UNRESOLVABLE)) as recorder:
            assert recorder is not None
            run_id = recorder.open(PassKind.HEALTH)
            recorder.progress(run_id, phase="x", done=1, total=2)
            recorder.close(run_id, outcome=PassRunOutcome.COMPLETE, exit_code=0)

    def test_nothing_is_written_to_stdout(self, capsys) -> None:
        """A bookkeeping failure must not corrupt a command's --json output."""
        with pass_run_recorder(_settings(_UNRESOLVABLE)) as recorder:
            assert recorder is not None
            recorder.open(PassKind.HEALTH)
        assert capsys.readouterr().out == ""


class TestTimeoutIsConfigured:
    def test_the_connect_timeout_is_short(self) -> None:
        """A long timeout would turn a missing database into a stalled pass."""
        assert 0 < PASS_RUN_DB_CONNECT_TIMEOUT_SECONDS <= 10
