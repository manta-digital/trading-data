"""Content-level tests for scripts/backup_metadata.sh (slice 915, task 2.3).

Runs the real script against a fixture-created ephemeral database carrying the
full migration chain — real hypertables, real caggs, real metadata tables —
and asserts on the *content* of the produced dump via ``pg_restore -l``, never
on exit codes alone (D6).

The load-bearing assertion is the derived-list property: a scratch table
created after the script was written appears in the next dump with no script
edit. That is what distinguishes a catalog-derived list from a hardcoded one
(success criterion 4).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import psycopg

_REPO_ROOT = Path(__file__).parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "backup_metadata.sh"

# pg_restore -l table entries look like:
#   "218; 1259 33637 TABLE public instruments trading_test_admin"
_TABLE_ENTRY_RE = re.compile(r"\bTABLE (?:DATA )?public (\S+)")


def _run_dump(db_url: str, dest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_SCRIPT), "--db-url", db_url, "--dest", str(dest)],
        capture_output=True,
        text=True,
        timeout=300,
    )


def _dumped_tables(dest: Path) -> tuple[Path, set[str]]:
    dumps = sorted(dest.glob("meta-*.dump"))
    assert dumps, f"no dump produced in {dest}"
    newest = dumps[-1]
    listing = subprocess.run(
        ["pg_restore", "-l", str(newest)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return newest, set(_TABLE_ENTRY_RE.findall(listing.stdout))


def test_dump_content_and_derived_list(migrated_db: str, tmp_path: Path) -> None:
    with psycopg.connect(migrated_db) as conn:
        hypertables = {
            r[0]
            for r in conn.execute(
                "SELECT hypertable_name FROM timescaledb_information.hypertables"
            )
        }
        caggs = {
            r[0]
            for r in conn.execute(
                "SELECT view_name FROM timescaledb_information.continuous_aggregates"
            )
        }
    assert hypertables and caggs, "fixture is missing the shapes this test exists for"

    result = _run_dump(migrated_db, tmp_path)
    assert result.returncode == 0, result.stderr
    _, tables = _dumped_tables(tmp_path)

    # The metadata core must be present…
    for expected in ("instruments", "schema_migrations", "acquisition_state"):
        assert expected in tables

    # …and nothing from the bar-data or derived tiers, nor runtime state.
    assert not tables & hypertables, f"hypertables leaked into dump: {tables & hypertables}"
    assert not tables & caggs, f"caggs leaked into dump: {tables & caggs}"
    assert "daemon_heartbeat" not in tables, "runtime state must be excluded deliberately"

    # Derived-list property: a table the script has never heard of appears in
    # the next dump with no script edit.
    with psycopg.connect(migrated_db) as conn:
        conn.execute("CREATE TABLE scratch_915_derived_proof (id int PRIMARY KEY)")
        conn.commit()
    rerun = _run_dump(migrated_db, tmp_path)
    assert rerun.returncode == 0, rerun.stderr
    _, tables_after = _dumped_tables(tmp_path)
    assert "scratch_915_derived_proof" in tables_after

    # No partial artifacts left behind either way.
    assert not list(tmp_path.glob("*.part"))


def test_refuses_empty_enumeration(ephemeral_db: str, tmp_path: Path) -> None:
    """A database with no metadata tables must be refused, not emptily dumped."""
    with psycopg.connect(ephemeral_db) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        conn.commit()

    result = _run_dump(ephemeral_db, tmp_path)
    assert result.returncode != 0
    assert "no tables" in result.stderr
    assert not list(tmp_path.glob("meta-*.dump"))
