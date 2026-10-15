"""Integration tests: migration 060 (pass_runs admits tick, slice 234).

Runs against a throwaway database the fixture creates and drops itself
(``ephemeral_db``, from ``test/conftest.py``). Requires
``MT_TIMESCALE_TEST_URL`` pointing at an admin connection.

A fresh apply renders 055's CHECK from the current enum, which already holds
``tick``, so a plain apply proves nothing. The fixture therefore puts the
database in production's state at 059: the CHECK rendered without ``tick``
and 060 not recorded. Applying the chain must then run 060 and admit ``tick``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import psycopg
import pytest

from manta_trading.data.acquisition.pass_runs import PassKind
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

_MIGRATION_ID = "060_pass_kind_tick"


def _apply(url: str) -> list[str]:
    db = TimescaleMinuteDataDB(conninfo=url)
    try:
        return db.apply_schema_migrations()
    finally:
        db.close()


def _rewind_to_059(url: str) -> None:
    """Render the CHECK as production had it before tick, and unrecord 060."""
    before_tick = ", ".join(
        f"'{kind.value}'" for kind in sorted(PassKind) if kind is not PassKind.TICK
    )
    with psycopg.connect(url) as conn:
        conn.execute("ALTER TABLE pass_runs DROP CONSTRAINT pass_runs_pass_check")
        conn.execute(
            "ALTER TABLE pass_runs ADD CONSTRAINT pass_runs_pass_check "
            f"CHECK (pass IN ({before_tick}))"
        )
        conn.execute(
            "DELETE FROM schema_migrations WHERE migration_id = %s", (_MIGRATION_ID,)
        )


def _insert(url: str, kind: str) -> None:
    with psycopg.connect(url) as conn:
        conn.execute(
            "INSERT INTO pass_runs (run_id, pass, hostname, pid, started_at) "
            "VALUES (%s, %s, 'testhost', 1234, %s)",
            (uuid.uuid4(), kind, datetime(2026, 10, 14, 13, 5, tzinfo=UTC)),
        )


@pytest.fixture
def at_059(ephemeral_db: str) -> str:
    _apply(ephemeral_db)
    _rewind_to_059(ephemeral_db)
    return ephemeral_db


class TestMigration060PassKindTick:
    def test_the_rewound_database_rejects_tick(self, at_059: str) -> None:
        """Guards the fixture: without 060 the old constraint refuses tick."""
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(at_059, PassKind.TICK.value)

    def test_060_applies_and_admits_tick(self, at_059: str) -> None:
        assert _apply(at_059) == [_MIGRATION_ID]
        _insert(at_059, PassKind.TICK.value)

    def test_every_kind_is_accepted_after_060(self, at_059: str) -> None:
        _apply(at_059)
        for kind in PassKind:
            _insert(at_059, kind.value)

    def test_unknown_pass_is_still_rejected(self, at_059: str) -> None:
        _apply(at_059)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(at_059, "sideways")

    def test_reapply_is_a_noop(self, at_059: str) -> None:
        _apply(at_059)
        assert _apply(at_059) == []
