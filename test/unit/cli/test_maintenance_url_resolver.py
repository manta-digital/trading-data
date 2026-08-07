"""Maintenance-URL resolution must fail loudly, never fall back (slice 913 D4).

The application credential and the migration credential are deliberately
separate. If ``_get_maintenance_url`` ever fell back to ``timescale_db_url``,
a machine configured with only the application credential would silently
attempt DDL as the DML-only role — restoring the single-credential coupling
this slice removes, and converting a clear configuration error into a
confusing privilege failure partway through a migration.

``test_unset_maintenance_url_does_not_fall_back`` is the regression guard for
exactly that. It must keep failing if anyone adds a fallback branch.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import typer

from manta_trading.cli.commands.data import _get_maintenance_url, _get_timescale_url

APP_URL = "postgresql://app@example.invalid:5432/trading"
MAINT_URL = "postgresql://migrate@example.invalid:5432/trading"

MAINT_VAR = "MT_TIMESCALE_MAINTENANCE_URL"
APP_VAR = "MT_TIMESCALE_DB_URL"


@dataclass
class _Settings:
    """Minimal stand-in for the fields the resolvers read."""

    timescale_db_url: str | None = None
    timescale_maintenance_url: str | None = None


class _Ctx:
    """Stands in for ``typer.Context``; the resolvers only touch ``obj``."""

    def __init__(self, settings: _Settings) -> None:
        self.obj = {"settings": settings}


def test_returns_the_maintenance_url_when_set() -> None:
    ctx = _Ctx(_Settings(timescale_maintenance_url=MAINT_URL))
    assert _get_maintenance_url(ctx) == MAINT_URL  # type: ignore[arg-type]


def test_unset_maintenance_url_does_not_fall_back(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The regression guard: app URL present, maintenance URL absent -> raise.

    Do not "fix" a failure here by adding a fallback to ``timescale_db_url``.
    The absence of that fallback is the point of the test.
    """
    ctx = _Ctx(_Settings(timescale_db_url=APP_URL))

    with pytest.raises(typer.Exit) as excinfo:
        _get_maintenance_url(ctx)  # type: ignore[arg-type]

    assert excinfo.value.exit_code == 1
    captured = capsys.readouterr()
    message = captured.out + captured.err
    assert MAINT_VAR in message, (
        f"the error must name {MAINT_VAR} so the operator knows what to set"
    )


def test_error_message_does_not_offer_the_application_url_as_a_substitute(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Naming the app var is fine, but only to say it will NOT be used."""
    ctx = _Ctx(_Settings(timescale_db_url=APP_URL))

    with pytest.raises(typer.Exit):
        _get_maintenance_url(ctx)  # type: ignore[arg-type]

    out = capsys.readouterr().err
    if APP_VAR in out:
        assert "not fall back" in out, (
            "mentioning the application variable is only acceptable as an "
            "explicit statement that it will not be substituted"
        )


def test_both_unset_still_names_the_maintenance_variable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctx = _Ctx(_Settings())

    with pytest.raises(typer.Exit):
        _get_maintenance_url(ctx)  # type: ignore[arg-type]

    assert MAINT_VAR in capsys.readouterr().err


def test_the_two_resolvers_read_independent_keys() -> None:
    """Each resolver reads only its own key — no cross-wiring."""
    ctx = _Ctx(_Settings(timescale_db_url=APP_URL, timescale_maintenance_url=MAINT_URL))
    assert _get_timescale_url(ctx) == APP_URL  # type: ignore[arg-type]
    assert _get_maintenance_url(ctx) == MAINT_URL  # type: ignore[arg-type]


def test_application_resolver_is_unaffected_by_a_missing_maintenance_key() -> None:
    """Read paths must keep working on a machine with no DDL credential."""
    ctx = _Ctx(_Settings(timescale_db_url=APP_URL))
    assert _get_timescale_url(ctx) == APP_URL  # type: ignore[arg-type]


def test_empty_string_is_treated_as_unset(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An exported-but-empty variable is a misconfiguration, not a value."""
    ctx = _Ctx(_Settings(timescale_maintenance_url=""))

    with pytest.raises(typer.Exit):
        _get_maintenance_url(ctx)  # type: ignore[arg-type]

    assert MAINT_VAR in capsys.readouterr().err
