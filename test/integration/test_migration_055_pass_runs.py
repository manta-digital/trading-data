"""Integration tests: migration 055 (pass_runs, slice 922).

Runs against a throwaway database the fixture creates and drops itself
(``ephemeral_db``, from ``test/conftest.py``), so nothing here can reach a
configured production database. Requires ``MT_TIMESCALE_TEST_URL`` pointing
at an *admin* connection on a TimescaleDB-equipped instance.

Verifies:
- The table exists with the exact column list the design specifies.
- 055 is recorded between 020 and 021 (it is position-critical: the
  data_status view builder references pass_runs, so a fresh database must
  create the table before 021 issues the view).
- The two enum CHECKs and the ended/outcome pairing CHECK all reject.
- Re-applying the chain is a no-op.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import psycopg
import pytest

from manta_trading.data.acquisition.pass_runs import PassKind, PassRunOutcome
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

_MIGRATION_ID = "055_create_pass_runs"

_EXPECTED_COLUMNS = [
    "run_id",
    "pass",
    "hostname",
    "pid",
    "walk_anchor_at",
    "started_at",
    "ended_at",
    "phase",
    "progress_done",
    "progress_total",
    "progress_updated_at",
    "outcome",
    "exit_code",
    "detail",
]


def _apply(url: str) -> list[str]:
    db = TimescaleMinuteDataDB(conninfo=url)
    try:
        return db.apply_schema_migrations()
    finally:
        db.close()


def _insert(url: str, **overrides: object) -> None:
    """Insert one pass_runs row, letting the caller break one field at a time."""
    row: dict[str, object] = {
        "run_id": uuid.uuid4(),
        "pass": str(PassKind.MINUTE),
        "hostname": "testhost",
        "pid": 1234,
        "started_at": datetime(2026, 9, 12, 13, 5, tzinfo=UTC),
        "ended_at": None,
        "outcome": None,
    }
    row.update(overrides)
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO pass_runs ({cols}) VALUES ({marks})",
                tuple(row.values()),
            )


class TestMigration055PassRuns:
    def test_table_has_the_designed_columns(self, ephemeral_db: str) -> None:
        _apply(ephemeral_db)
        with psycopg.connect(ephemeral_db) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'pass_runs' ORDER BY ordinal_position"
                )
                names = [r[0] for r in cur.fetchall()]
        assert names == _EXPECTED_COLUMNS

    def test_055_recorded_between_020_and_021(self, ephemeral_db: str) -> None:
        applied = _apply(ephemeral_db)
        assert _MIGRATION_ID in applied
        assert applied.index("020_drop_coverage_gaps") < applied.index(_MIGRATION_ID)
        assert applied.index(_MIGRATION_ID) < applied.index("021_data_status_view")

    def test_reapply_is_a_noop(self, ephemeral_db: str) -> None:
        _apply(ephemeral_db)
        assert _apply(ephemeral_db) == []

    def test_every_pass_kind_is_accepted(self, ephemeral_db: str) -> None:
        _apply(ephemeral_db)
        for kind in PassKind:
            _insert(ephemeral_db, **{"pass": str(kind)})

    def test_unknown_pass_kind_is_rejected(self, ephemeral_db: str) -> None:
        _apply(ephemeral_db)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(ephemeral_db, **{"pass": "sideways"})

    def test_every_outcome_is_accepted(self, ephemeral_db: str) -> None:
        _apply(ephemeral_db)
        ended = datetime(2026, 9, 12, 13, 30, tzinfo=UTC)
        for outcome in PassRunOutcome:
            _insert(ephemeral_db, ended_at=ended, outcome=str(outcome))

    def test_unknown_outcome_is_rejected(self, ephemeral_db: str) -> None:
        _apply(ephemeral_db)
        ended = datetime(2026, 9, 12, 13, 30, tzinfo=UTC)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(ephemeral_db, ended_at=ended, outcome="MOSTLY_FINE")

    def test_ended_without_outcome_is_rejected(self, ephemeral_db: str) -> None:
        """An ended run must say how it ended; an open run must not."""
        _apply(ephemeral_db)
        ended = datetime(2026, 9, 12, 13, 30, tzinfo=UTC)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(ephemeral_db, ended_at=ended, outcome=None)

    def test_outcome_without_ended_is_rejected(self, ephemeral_db: str) -> None:
        _apply(ephemeral_db)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(ephemeral_db, ended_at=None, outcome=str(PassRunOutcome.COMPLETE))
