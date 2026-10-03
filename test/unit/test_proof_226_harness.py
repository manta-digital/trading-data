"""The slice 226 proof harness: dispatcher, guard, URLs, space floor, report."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from manta_trading.data.tick.constants import TICK_ENV_PREFIX, TICK_PROOF_DB_NAME

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import proof_226_tick  # noqa: E402
from proof_226 import common, guard  # noqa: E402

#: TD 2's step table, plus ``archive-check`` (task 11.3).
TD2_STEPS = {
    "rebuild",
    "workers",
    "batch",
    "jobs",
    "mapping",
    "size",
    "layouts",
    "queries",
    "contention",
    "final",
    "drop-proof",
    "archive-check",
}


class FakeConn:
    """Answers ``current_database()``; records anything else it is sent."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.sent: list[str] = []

    def execute(self, query: Any, params: Any = None, /) -> Any:
        if query == "SELECT current_database()":
            return _Row((self.name,))
        self.sent.append(str(query))
        return _Row((0,))


class _Row:
    rowcount = 0

    def __init__(self, row: tuple[Any, ...]) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...]:
        return self._row


def test_dispatcher_knows_every_step() -> None:
    assert set(proof_226_tick.STEPS) == TD2_STEPS


def test_unknown_step_exits_non_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert proof_226_tick.main(["nope"]) == 2
    assert "usage" in capsys.readouterr().err


def test_unimplemented_step_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(proof_226_tick.STEPS, "rebuild", None)
    assert proof_226_tick.main(["rebuild"]) == 3
    assert "not implemented" in capsys.readouterr().err


def test_destructive_functions_are_registered() -> None:
    assert "reset_proof_database" in guard.DESTRUCTIVE


@pytest.mark.parametrize("name", sorted(guard.DESTRUCTIVE))
def test_every_destructive_function_refuses_another_database(name: str) -> None:
    conn = FakeConn("trading_tick")
    with pytest.raises(guard.NotProofDatabaseError, match="'trading_tick'"):
        guard.DESTRUCTIVE[name](conn)
    assert conn.sent == [], "nothing may be sent before the guard passes"


def test_the_guard_passes_the_proof_database() -> None:
    conn = FakeConn(TICK_PROOF_DB_NAME)
    guard.reset_proof_database(conn)
    assert conn.sent[0] == "TRUNCATE tick_trade, tick_ingest_ledger"


def test_proof_urls_load_from_the_env_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "MT_PROOF_226_DB_URL='postgresql://a@h:5433/trading_tick_proof'\n"
        "MT_PROOF_226_MAINTENANCE_URL=postgresql://b@h:5433/trading_tick_proof\n"
    )
    urls = common.load_proof_urls(env)
    assert urls.db_url.startswith("postgresql://a@")
    assert urls.maintenance_url.startswith("postgresql://b@")


def test_missing_proof_urls_raise_naming_them(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("MT_PROOF_226_DB_URL=postgresql://a@h/x\n")
    with pytest.raises(common.ProofSetupError, match="MT_PROOF_226_MAINTENANCE_URL"):
        common.load_proof_urls(env)


def test_proof_url_names_are_outside_the_tick_prefix() -> None:
    for name in (common.PROOF_DB_URL_ENV, common.PROOF_MAINTENANCE_URL_ENV):
        assert not name.upper().startswith(TICK_ENV_PREFIX)


def test_free_space_below_the_floor_refuses(tmp_path: Path) -> None:
    with pytest.raises(common.ProofSetupError, match="below the"):
        common.require_free_space(tmp_path, floor_bytes=10**18)
    assert common.require_free_space(tmp_path, floor_bytes=1) > 0


def test_report_writes_frontmatter_and_body(tmp_path: Path) -> None:
    report = common.Report("size", "Proof: size")
    report.table(("tier", "bytes/row"), [("trades", 12.5)])
    path = report.write(tmp_path)
    text = path.read_text()
    assert path.name.endswith("-226-proof-size.md")
    assert text.startswith("---\ndocType: note\n")
    assert "| trades | 12.5 |" in text


# -- mapping, size, jobs ------------------------------------------------------------

REAL = Path(__file__).resolve().parents[1] / "fixtures" / "databento" / "real"
TRADES_SLICE = REAL / "glbx-mdp3-20240903.trades.dbn.zst"


def test_a_real_slice_maps_every_record_on_its_day() -> None:
    from proof_226.mapping import check_file

    result = check_file(1, date(2024, 9, 3), TRADES_SLICE)
    assert result.records > 0
    assert result.misses == 0


def test_a_day_no_interval_covers_misses_every_record() -> None:
    from proof_226.mapping import check_file

    result = check_file(1, date(2020, 1, 1), TRADES_SLICE)
    assert result.misses == result.records > 0
    assert len(result.missing_ids) <= 10


def test_ids_on_uses_a_half_open_interval() -> None:
    from proof_226.mapping import ids_on

    from manta_trading.data.tick.provider import SymbolInterval

    mappings = {"ESZ4": (SymbolInterval(date(2024, 9, 3), date(2024, 9, 4), 7),)}
    assert ids_on(mappings, date(2024, 9, 3)) == {7}
    assert ids_on(mappings, date(2024, 9, 4)) == set()


def test_tier_bytes_come_from_single_tier_chunks_only() -> None:
    from proof_226.size import tier_rows_and_bytes

    rows, table, mixed = tier_rows_and_bytes(
        [
            ("c1", "trades", 10),
            ("c2", "tbbo", 5),
            ("c3", "trades", 1),
            ("c3", "tbbo", 1),
        ],
        {"c1": 1000, "c2": 700, "c3": 99},
    )
    assert rows == {"trades": 11, "tbbo": 6}
    assert table == {"trades": 1000, "tbbo": 700}
    assert mixed == ["c3"]


def test_skew_reports_top_shares_per_chunk() -> None:
    from proof_226.size import skew

    [row] = skew([("c1", 1, 80), ("c1", 2, 15), ("c1", 3, 5)])
    assert row == ("c1", 3, 100, "80.0 %", "100.0 %", 15)


def test_spread_classes_are_the_sdk_spread_codes() -> None:
    from proof_226.size import SPREAD_CLASSES

    assert SPREAD_CLASSES == ("S", "M", "T")


def test_wait_budget_rule() -> None:
    from proof_226.jobs import wait_budget

    from manta_trading.data.tick.constants import TICK_WAIT_BUDGET_SECONDS

    assert wait_budget(287) == TICK_WAIT_BUDGET_SECONDS
    assert wait_budget(1000) == 2000


def test_workers_rule_keeps_two_below_one_and_a_half_times() -> None:
    from proof_226.workers import verdict

    assert verdict({1: 300.0, 2: 200.0, 4: 150.0}, current=2).startswith(
        "4 workers are 1.33× faster than 2 (< 1.5×): keep 2"
    )
    assert "change to 4 **if** the contention run" in verdict(
        {1: 300.0, 2: 200.0, 4: 100.0}, current=2
    )


def test_batch_count_matches_iter_batches() -> None:
    from proof_226.batch import batch_count

    assert batch_count(1000, 48, 48 * 100) == 10
    assert batch_count(1001, 48, 48 * 100) == 11


def test_batch_rule() -> None:
    from proof_226.batch import verdict

    mib = 1024 * 1024
    flat = {8 * mib: 100.0, 32 * mib: 100.0, 128 * mib: 95.0}
    # The 2026-10-03 host run: ~469 MiB fixed, ~2.2x per worker at 2 workers.
    measured = {8 * mib: 469 * mib, 32 * mib: 667 * mib, 128 * mib: 1020 * mib}
    assert verdict(flat, measured, workers=2).startswith("keep 32 MiB")
    steep = {b: 10 * b for b in measured}  # 5x per worker at 2 workers
    assert verdict(flat, steep, workers=2).startswith("change: a worker holds 5.0×")
    faster = {**flat, 128 * mib: 80.0}
    assert verdict(faster, measured, workers=2).startswith("change to 128 MiB")


def test_budget_memory_separates_fixed_from_scaling() -> None:
    from proof_226.batch import budget_memory

    per_worker, fixed = budget_memory({8: 100 + 3 * 8, 32: 100 + 3 * 32}, workers=1)
    assert (round(per_worker, 6), round(fixed, 6)) == (3.0, 100.0)


def _layout_result(name: str, bytes_per_row: float, q_ms: float) -> Any:
    from proof_226 import layouts
    from proof_226.queries import QueryResult

    layout = layouts.LAYOUT_A if name == "A" else layouts.LAYOUT_B
    results = [QueryResult("trades", q, 1.0, q_ms, 1.0, 1) for q in ("Q1", "Q4")]
    return layouts.LayoutResult(layout, None, {}, bytes_per_row, results, 0.1)


def test_layout_rule() -> None:
    from proof_226.layouts import decide

    assert decide(
        _layout_result("A", 10, 100), _layout_result("B", 10.5, 105)
    ).startswith("A: within")
    assert decide(_layout_result("A", 10, 100), _layout_result("B", 6, 100)).startswith(
        "B: lower"
    )
    assert decide(
        _layout_result("A", 10, 100), _layout_result("B", 6, 1500)
    ).startswith("A: B is smaller but misses")


def test_final_refuses_to_compress_a_database_the_tick_url_does_not_name() -> None:
    from proof_226 import final

    conn = FakeConn("trading_tick_proof")
    with pytest.raises(final.NotProductionTickDatabaseError, match="'trading_tick'"):
        final.compress_eligible(conn, "trading_tick")
    assert conn.sent == []


def test_final_refuses_below_the_free_space_floor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from functools import partial

    from proof_226 import final

    monkeypatch.setattr(
        final,
        "require_free_space",
        partial(common.require_free_space, tmp_path, floor_bytes=10**18),
    )
    conn = FakeConn("trading_tick")
    with pytest.raises(common.ProofSetupError, match="below the"):
        final.compress_eligible(conn, "trading_tick")
    assert conn.sent == []


def test_final_compresses_the_named_database() -> None:
    from proof_226 import final

    conn = FakeConn("trading_tick")
    final.compress_eligible(conn, "trading_tick")
    assert "compress_chunk" in conn.sent[0]


def test_database_named_reads_the_url_path() -> None:
    from proof_226.final import database_named

    assert database_named("postgresql://u:p@manta9000:5433/trading_tick") == (
        "trading_tick"
    )
