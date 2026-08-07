"""DDL commands resolve the maintenance credential; read paths do not (913 D4).

Each command below is asserted at the *routing* level: with the maintenance
key unset it must fail naming ``MT_TIMESCALE_MAINTENANCE_URL``, before any
connection is attempted. The application URL points at an unroutable host, so
a command that wrongly fell back would surface a connection error instead —
which is precisely the confusing failure D4 exists to prevent.

Read-only variants (``--dry-run``, ``--validate-only``, ``status``,
``assess``) must never demand the maintenance key: an operator with only the
application credential has to be able to inspect state.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from manta_trading.cli.app import app

MAINT_VAR = "MT_TIMESCALE_MAINTENANCE_URL"
APP_VAR = "MT_TIMESCALE_DB_URL"

#: Deliberately unroutable: if a command falls back to this instead of failing
#: on the missing maintenance key, the error looks like a network problem.
UNROUTABLE_APP_URL = "postgresql://app@example.invalid:5432/trading"

DDL_COMMANDS = [
    pytest.param(["data", "migrate", "apply"], id="migrate-apply"),
    pytest.param(["data", "init"], id="init"),
    pytest.param(["data", "restore", "run"], id="restore-run"),
    pytest.param(["data", "rechunk"], id="rechunk"),
    pytest.param(["data", "caggs", "refresh"], id="caggs-refresh"),
]

READ_ONLY_COMMANDS = [
    pytest.param(["data", "migrate", "status"], id="migrate-status"),
    pytest.param(["data", "restore", "assess"], id="restore-assess"),
    pytest.param(["data", "init", "--validate-only"], id="init-validate-only"),
    pytest.param(["data", "rechunk", "--dry-run"], id="rechunk-dry-run"),
]

#: Read-only commands reach a real connection attempt (that is the point — they
#: did not stop at a credential check), so the host must fail fast rather than
#: sit in TCP retry until the suite's 30s timeout fires. ``connect_timeout``
#: is honored by libpq; the address is reserved by RFC 5737 as unroutable.
FAST_FAIL_APP_URL = "postgresql://app@192.0.2.1:5432/trading?connect_timeout=1"


@pytest.fixture
def app_credential_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The realistic misconfiguration: application URL set, maintenance absent."""
    monkeypatch.setenv(APP_VAR, UNROUTABLE_APP_URL)
    monkeypatch.delenv(MAINT_VAR, raising=False)


@pytest.mark.parametrize("command", DDL_COMMANDS)
def test_ddl_command_requires_the_maintenance_key(
    command: list[str], app_credential_only: None
) -> None:
    """Fails loudly naming the missing key, rather than falling back."""
    result = CliRunner().invoke(app, command)

    assert result.exit_code != 0
    assert MAINT_VAR in result.output, (
        f"{' '.join(command)} must name {MAINT_VAR} when it is unset; "
        f"got: {result.output!r}"
    )


@pytest.mark.parametrize("command", DDL_COMMANDS)
def test_ddl_command_does_not_attempt_a_connection_first(
    command: list[str], app_credential_only: None
) -> None:
    """The failure is configuration, not connectivity.

    A fallback to the application URL would produce a psycopg connection
    error against the unroutable host. Seeing that text means the routing
    regressed.
    """
    result = CliRunner().invoke(app, command)

    lowered = result.output.lower()
    for connection_noise in ("could not translate", "connection refused", "timeout"):
        assert connection_noise not in lowered, (
            f"{' '.join(command)} appears to have connected using {APP_VAR} "
            f"instead of demanding {MAINT_VAR}: {result.output!r}"
        )


@pytest.mark.parametrize("command", READ_ONLY_COMMANDS)
def test_read_only_command_does_not_require_the_maintenance_key(
    command: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inspection must work with only the application credential.

    The connection layer is stubbed out. Letting these reach a real connect
    is not merely slow: ``ConnectionPool`` retries on a background thread and
    ignores ``connect_timeout``, so an unroutable host hangs until the suite
    timeout rather than failing fast. What is under test is credential
    *routing*, which is decided before any socket is opened.
    """
    monkeypatch.setenv(APP_VAR, FAST_FAIL_APP_URL)
    monkeypatch.delenv(MAINT_VAR, raising=False)

    class _Unreachable(Exception):
        pass

    def _refuse(*args: object, **kwargs: object) -> None:
        raise _Unreachable("connection attempted (expected for a read path)")

    monkeypatch.setattr("psycopg.connect", _refuse)
    monkeypatch.setattr("psycopg_pool.ConnectionPool.__init__", _refuse)

    result = CliRunner().invoke(app, command)

    assert MAINT_VAR not in result.output, (
        f"{' '.join(command)} is read-only and must not require {MAINT_VAR}; "
        f"got: {result.output!r}"
    )
