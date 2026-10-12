"""The ``tick get`` writers on string buffers (slice 230, TD5, TD11; FR9, FR10)."""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from typing import Any

import pytest

from manta_trading.cli.commands.tick_get_writers import writer_for
from manta_trading.constants import Granularity
from manta_trading.data.tick.constants import TICK_GET_TICKS, UNDEF_INT64
from manta_trading.data.tick.get_plan import (
    ContractTarget,
    DisplaySegment,
    GetPlan,
    Incomplete,
    ProductTarget,
)
from manta_trading.data.tick.roll_rules import DEFAULT_ROLL_RULE
from manta_trading.data.tick.roll_types import Unresolved
from manta_trading.data.tick.tick_reads import BAR_COLUMNS, TICK_COLUMNS
from manta_trading.data.tick.tick_status import TickSessionStatus

D16, D17, D18 = date(2024, 9, 16), date(2024, 9, 17), date(2024, 9, 18)
TS = 1_726_493_400_000_000_007  # 2024-09-16T13:30:00.000000007Z
PX = 5_650_250_000_000  # 5650.25

PRODUCT = GetPlan(
    ProductTarget("ES", DEFAULT_ROLL_RULE),
    D16,
    date(2024, 9, 20),
    (),
    (
        DisplaySegment(D16, D17, "ESU4", 118),
        DisplaySegment(D18, date(2024, 9, 19), "ESZ4", 183748),
    ),
    (Unresolved(date(2024, 9, 3), "no ranking session held"),),
    (Incomplete(D18, TickSessionStatus.AWAITING_INGEST),),
    date(2024, 8, 30),
)
CONTRACT = GetPlan(ContractTarget("ESZ4", 183748), D16, D18, (), (), (), (), None)
#: A trades-tier row (BBO null) and a tbbo row with an undefined ask.
TICK_ROWS = [
    (TS, D16, "ESZ4", 183748, PX, 2, "A", None, None, None, None, None, None, 9),
    (TS + 1, D16, "ESZ4", 183748, PX, 1, "B", PX, UNDEF_INT64, 3, 0, 1, 0, 10),
]
BAR_ROWS = [(TS, D16, "ESU4", 118, PX, PX + 250_000_000, PX, PX, 7)]


def _run(
    what: str,
    plan: GetPlan,
    batches: list[list[Any]],
    fmt: str,
    *,
    cut: str | None = None,
) -> tuple[str, str]:
    out, err = io.StringIO(), io.StringIO()
    writer = writer_for(
        what, json_output=fmt == "json", csv_output=fmt == "csv", out=out, err=err
    )
    writer.begin(plan)
    for batch in batches:
        writer.rows(batch)
    if cut is None:
        writer.finish(3 if plan.unresolved or plan.incomplete else 0)
    else:
        writer.partial(sum(map(len, batches)), cut)
    return out.getvalue(), err.getvalue()


def test_json_has_td5_keys_in_order_and_exact_prices() -> None:
    out, err = _run(TICK_GET_TICKS, CONTRACT, [TICK_ROWS[:1], TICK_ROWS[1:]], "json")
    body = json.loads(out)

    assert list(body) == [
        "target", "what", "start", "end", "segments", "unresolved",
        "incomplete", "rows", "row_count", "complete", "error", "exit_code",
    ]  # fmt: skip
    assert body["target"] == {"contract": "ESZ4", "instrument_id": 183748}
    assert (body["row_count"], body["complete"], body["error"]) == (2, True, None)
    first, second = body["rows"]
    assert list(first) == list(TICK_COLUMNS)
    assert first["price"] == "5650.25" and first["bid_px"] is None  # FR9
    assert first["ts_event"] == "2024-09-16T13:30:00.000000007Z"
    assert second["ask_px"] is None  # UNDEF, never 9223372036.854775807
    assert err == ""


def test_json_product_target_and_lists() -> None:
    body = json.loads(_run("1m", PRODUCT, [BAR_ROWS], "json")[0])

    assert body["target"] == {
        "product": "ES",
        "rule": "v.0",
        "is_default": True,
        "definitions_as_of": "2024-08-30",
    }
    assert body["segments"][1] == {
        "first_session": "2024-09-18",
        "last_session": "2024-09-19",
        "raw_symbol": "ESZ4",
        "instrument_id": 183748,
    }
    assert body["unresolved"] == [
        {"session_date": "2024-09-03", "reason": "no ranking session held"}
    ]
    assert body["incomplete"] == [
        {"session_date": "2024-09-18", "status": "awaiting_ingest"}
    ]
    assert body["exit_code"] == 3
    assert list(body["rows"][0]) == list(BAR_COLUMNS)
    assert body["rows"][0]["high"] == "5650.5"


def test_csv_stdout_is_header_and_rows_only() -> None:
    out, err = _run(TICK_GET_TICKS, PRODUCT, [TICK_ROWS], "csv")
    rows = list(csv.reader(io.StringIO(out)))

    assert rows[0] == list(TICK_COLUMNS)
    assert len(rows) == 3
    assert rows[1][4] == "5650.25" and rows[1][7] == ""  # undefined is empty
    assert rows[2][8] == ""
    assert "ESU4" not in out and "unresolved" not in out  # FR10
    assert "2024-09-16 → 2024-09-17  ESU4 (118)" in err
    assert "unresolved 2024-09-03" in err and "incomplete 2024-09-18" in err


def test_text_header_rows_footer_and_lists() -> None:
    out, _ = _run(str(Granularity.M1), PRODUCT, [BAR_ROWS], "text")
    lines = out.splitlines()

    assert lines[0].startswith("ES  v.0 (default)  definitions as of 2024-08-30  1m")
    assert "(end exclusive)" in lines[0]
    assert lines[1] == "  2024-09-16 → 2024-09-17  ESU4 (118)"
    assert lines[3].split()[:2] == ["time", "session_date"]
    assert lines[4].split()[4:6] == ["5650.25", "5650.5"]
    assert lines[5] == "1 row(s)"
    assert lines[6].startswith("unresolved 2024-09-03")
    assert lines[7] == "incomplete 2024-09-18  awaiting_ingest"


def test_text_shows_undefined_as_a_dash() -> None:
    out, _ = _run(TICK_GET_TICKS, CONTRACT, [TICK_ROWS], "text")

    assert out.splitlines()[0].startswith(
        "ESZ4 (183748)  ticks  2024-09-16 → 2024-09-18"
    )
    assert "9223372036" not in out
    assert out.splitlines()[-1] == "2 row(s)"


@pytest.mark.parametrize("fmt", ["text", "csv", "json"])
def test_an_empty_plan_prints_the_header_and_zero_rows(fmt: str) -> None:
    out, _ = _run(TICK_GET_TICKS, CONTRACT, [], fmt)

    if fmt == "json":
        assert json.loads(out)["rows"] == [] and json.loads(out)["row_count"] == 0
    elif fmt == "csv":
        assert out == ",".join(TICK_COLUMNS) + "\n"
    else:
        assert out.splitlines()[-1] == "0 row(s)"


def test_partial_json_stays_valid() -> None:
    out, _ = _run(TICK_GET_TICKS, CONTRACT, [TICK_ROWS], "json", cut="server closed")
    body = json.loads(out)

    assert (body["complete"], body["error"], body["exit_code"]) == (
        False,
        "server closed",
        4,
    )
    assert body["row_count"] == 2


def test_partial_csv_marks_stderr_only() -> None:
    out, err = _run(TICK_GET_TICKS, CONTRACT, [TICK_ROWS], "csv", cut="server closed")

    assert "partial" not in out
    assert err.splitlines()[-1] == "partial output: 2 rows written, then: server closed"


def test_partial_text_replaces_the_footer() -> None:
    out, err = _run(TICK_GET_TICKS, CONTRACT, [TICK_ROWS], "text", cut="server closed")

    assert out.splitlines()[-1] == "PARTIAL: 2 row(s), then: server closed"
    assert "row(s)\n" not in out.replace("PARTIAL: 2 row(s), then", "")
    assert err == "server closed\n"


def test_nothing_held_says_so_in_the_header() -> None:
    plan = GetPlan(ContractTarget("ESZ9", 42004134), None, None, (), (), (), (), None)

    out, _ = _run(TICK_GET_TICKS, plan, [], "text")

    assert out.splitlines()[0] == "ESZ9 (42004134)  ticks  nothing held"
    assert "None" not in out
