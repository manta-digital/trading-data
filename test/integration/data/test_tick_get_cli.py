"""``mt data tick get`` end to end (slice 230, FR1, FR5–FR7, FR10a; TD5, TD11).

The September 2024 roll is seeded (``roll_seed``) plus a few ESZ4 ticks in
session 2024-09-18 (22:00 UTC 09-17 → 21:00 UTC 09-18); the calendar is the
scratch production database's CME_EQUITY.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from tick_support.cli import clear_tick_env, cli_settings, closed_port
from tick_support.roll_seed import ESZ4, SeededRoll, seed_september_roll
from tick_support.rows import FILE_COLUMNS, insert_request, insert_unit
from tick_support.ticks import Tick, seed_ticks
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands import tick_get_writers
from manta_trading.data.tick import tick_reads
from manta_trading.data.tick.constants import TickSchema, UnitState

runner = CliRunner(env={"COLUMNS": "200"})
GET = ["data", "tick", "get"]
SESSION_0918 = ["--start", "2024-09-18", "--end", "2024-09-19"]
N_TICKS = 5


def _ns(hour: int, minute: int = 0) -> int:
    return int(datetime(2024, 9, 18, hour, minute, tzinfo=UTC).timestamp()) * 10**9


@pytest.fixture
def seeded(
    migrated_tick_db: str, session_migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SeededRoll]:
    clear_tick_env(monkeypatch)
    roll = seed_september_roll(migrated_tick_db)
    ticks = [
        Tick(_ns(14, n), price=5_650_250_000_000 + n, sequence=n)
        for n in range(N_TICKS)
    ]
    seed_ticks(
        migrated_tick_db, roll.units[date(2024, 9, 18)], ESZ4.instrument_id, ticks
    )
    with cli_settings(migrated_tick_db, session_migrated_db):
        yield roll


def _invoke(*args: str) -> Any:
    return runner.invoke(app, [*GET, *args])


def test_a_clean_contract_read_exits_zero_with_every_tick(seeded: SeededRoll) -> None:
    result = _invoke("ticks", "--contract", "ESZ4", *SESSION_0918, "--json")

    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["row_count"] == N_TICKS
    assert body["rows"][0]["price"] == "5650.25"
    assert body["target"] == {"contract": "ESZ4", "instrument_id": ESZ4.instrument_id}
    assert (body["complete"], body["exit_code"]) == (True, 0)


def test_product_bars_follow_the_schedule(seeded: SeededRoll) -> None:
    result = _invoke(
        "1h", "--product", "ES", "--start", "2024-09-16", "--end", "2024-09-20"
    )

    assert result.exit_code == 0, result.output
    assert "v.0 (default)" in result.stdout
    assert "2024-09-16 → 2024-09-17  ESU4 (118)" in result.stdout
    assert "2024-09-18 → 2024-09-19  ESZ4 (183748)" in result.stdout
    assert "1 row(s)" in result.stdout  # the five ticks fall in one hour


def test_unresolved_sessions_print_and_exit_three(seeded: SeededRoll) -> None:
    result = _invoke(
        "1d",
        "--product",
        "ES",
        "--start",
        "2024-09-03",
        "--end",
        "2024-09-06",
        "--json",
    )

    assert result.exit_code == 3, result.output
    body = json.loads(result.stdout)
    assert [u["session_date"] for u in body["unresolved"]] == [
        "2024-09-03",
        "2024-09-04",
    ]
    assert body["exit_code"] == 3


def test_an_incomplete_session_is_read_listed_and_exits_three(
    seeded: SeededRoll, migrated_tick_db: str
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        request = insert_request(conn, schema=TickSchema.TBBO.value)
        insert_unit(
            conn,
            request,
            unit_date=date(2024, 9, 18),
            state=UnitState.VERIFIED.value,
            **FILE_COLUMNS,
        )

    result = _invoke("ticks", "--contract", "ESZ4", *SESSION_0918, "--csv")

    assert result.exit_code == 3, result.output
    assert len(result.stdout.splitlines()) == 1 + N_TICKS
    assert "incomplete 2024-09-18  awaiting_ingest" in result.stderr


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["ticks"], "exactly one of --contract and --product"),
        (["ticks", "--contract", "ESZ4", "--product", "ES"], "exactly one of"),
        (["ticks", "--contract", "ESZ4", "--rule", "v.0"], "--rule goes with"),
        (["ticks", "--contract", "ESZ4", "--csv", "--json"], "exclusive"),
        (["2m", "--contract", "ESZ4"], "WHAT must be one of"),
        (["1m", "--product", "ES", "--rule", "n.0"], "unknown roll method"),
        (["1m", "--product", "ZZ"], "--product ZZ is not in the universe"),
        (["ticks", "--contract", "ESZ9"], "no definition held for contract ESZ9"),
        (
            ["1m", "--product", "ES", "--start", "2040-01-02", "--end", "2040-01-09"],
            "mt data extend",
        ),
        (
            ["1m", "--product", "ES", "--start", "2024-09-10", "--end", "2024-09-10"],
            "is empty",
        ),
    ],
)
def test_refusals_exit_one(seeded: SeededRoll, args: list[str], message: str) -> None:
    result = _invoke(*args)

    assert result.exit_code == 1, result.output
    assert message in " ".join(result.output.split())


def test_an_ambiguous_contract_exits_one_listing_both(
    seeded: SeededRoll, migrated_tick_db: str
) -> None:
    from tick_support.rows import insert_definition

    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        insert_definition(
            conn, seeded.definition_unit, instrument_id=777, raw_symbol="ESZ4"
        )

    result = _invoke("ticks", "--contract", "ESZ4")

    assert result.exit_code == 1, result.output
    assert "777" in result.output and str(ESZ4.instrument_id) in result.output


def test_a_pending_tick_migration_exits_one(
    ephemeral_tick_db: str, session_migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_tick_env(monkeypatch)
    with cli_settings(ephemeral_tick_db, session_migrated_db):
        result = _invoke("ticks", "--contract", "ESZ4")

    assert result.exit_code == 1, result.output
    assert "mt data migrate apply --track tick" in " ".join(result.output.split())


def test_an_unset_tick_url_exits_one(
    session_migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_tick_env(monkeypatch)
    with cli_settings(None, session_migrated_db):
        result = _invoke("ticks", "--contract", "ESZ4")

    assert result.exit_code == 1, result.output
    assert "MT_TICK_DB_URL" in result.output


def test_an_unreachable_tick_database_exits_four(
    session_migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_tick_env(monkeypatch)
    dead = f"postgresql://nobody@127.0.0.1:{closed_port()}/none"
    with cli_settings(dead, session_migrated_db):
        result = _invoke("ticks", "--contract", "ESZ4")

    assert result.exit_code == 4, result.output


# -- TD11: a read that stops partway, and a reader that leaves ---------------


def _kill_after_first_batch(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Terminate ``get``'s backend once its first batch is written."""
    monkeypatch.setattr(tick_reads, "TICK_GET_FETCH_ROWS", 2)
    make = tick_get_writers.writer_for

    def writer_for(*args: Any, **kwargs: Any) -> Any:
        writer = make(*args, **kwargs)
        rows = writer.rows

        def rows_then_kill(batch: Any) -> None:
            rows(batch)
            with psycopg.connect(url, autocommit=True) as killer:
                killer.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    " WHERE datname = current_database() AND pid <> pg_backend_pid()"
                    " AND backend_type = 'client backend'"
                )

        writer.rows = rows_then_kill  # type: ignore[method-assign]
        return writer

    monkeypatch.setattr(tick_get_writers, "writer_for", writer_for)


def test_partial_json_stays_valid_and_exits_four(
    seeded: SeededRoll, migrated_tick_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _kill_after_first_batch(migrated_tick_db, monkeypatch)

    result = _invoke("ticks", "--contract", "ESZ4", *SESSION_0918, "--json")

    assert result.exit_code == 4, result.output
    body = json.loads(result.stdout)
    assert (body["complete"], body["row_count"], body["exit_code"]) == (False, 2, 4)
    assert body["error"]


def test_partial_csv_keeps_stdout_clean_and_exits_four(
    seeded: SeededRoll, migrated_tick_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _kill_after_first_batch(migrated_tick_db, monkeypatch)

    result = _invoke("ticks", "--contract", "ESZ4", *SESSION_0918, "--csv")

    assert result.exit_code == 4, result.output
    assert len(result.stdout.splitlines()) == 1 + 2
    assert "partial output: 2 rows written, then: " in result.stderr


def test_partial_text_ends_with_the_partial_footer(
    seeded: SeededRoll, migrated_tick_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _kill_after_first_batch(migrated_tick_db, monkeypatch)

    result = _invoke("ticks", "--contract", "ESZ4", *SESSION_0918)

    assert result.exit_code == 4, result.output
    assert result.stdout.splitlines()[-1].startswith("PARTIAL: 2 row(s), then: ")
    assert result.stderr


def test_a_reader_that_closes_the_pipe_gets_exit_zero(
    seeded: SeededRoll,
    migrated_tick_db: str,
    session_migrated_db: str,
    tmp_path: Path,
) -> None:
    """Enough rows to overflow the pipe; the reader takes one line and leaves.
    The child runs outside the repo (no ``.env``) with only the two URLs."""
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO tick_trade (ts_event, ts_recv, instrument_id, publisher_id,"
            " sequence, price, size, action, side, flags, depth, ts_in_delta,"
            " sequence_ordinal, unit_id)"
            " SELECT %s + n, %s + n, %s, 1, 1000 + n, 1, 1, 'T', 'A', 0, 0, 0, 0, %s"
            " FROM generate_series(1, 20000) AS n",
            (_ns(15), _ns(15), ESZ4.instrument_id, seeded.units[date(2024, 9, 18)]),
        )
    env = {
        "PATH": "/usr/bin:/bin",
        "MT_TICK_DB_URL": migrated_tick_db,
        "MT_TIMESCALE_DB_URL": session_migrated_db,
        "MT_LOG_LEVEL": "WARNING",  # INFO lines are not errors; keep stderr bare
    }
    command = [sys.executable, "-c", "from manta_trading.cli.app import app; app()"]
    child = subprocess.Popen(
        [*command, *GET, "ticks", "--contract", "ESZ4", *SESSION_0918],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=tmp_path,
        env=env,
    )
    assert child.stdout is not None and child.stderr is not None
    child.stdout.readline()
    child.stdout.close()
    stderr = child.stderr.read()
    assert child.wait(timeout=60) == 0, stderr.decode()
    assert stderr == b""
    with psycopg.connect(migrated_tick_db) as conn:
        open_transactions = conn.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
            " AND state LIKE 'idle in transaction%%'"
        ).fetchone()
    assert open_transactions == (0,)
