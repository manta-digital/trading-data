"""The query set Q1–Q5 and the ``queries`` step (LLD 226 TD3).

Targets come from the data: in each tier's range, the instrument-session with
the most records in the ledger. Every query runs under ``statement_timeout``
and ``EXPLAIN (ANALYZE, BUFFERS)``: planning and execution are recorded
apart (chunk count shows in planning), with the shared-buffer hit ratio.
Values are bound client-side because ``EXPLAIN`` takes no server parameters;
every value is an integer read from the database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, LiteralString

import psycopg

from manta_trading.data.tick.constants import STORED_TIERS, TICK_TRADE_CHUNK_INTERVAL
from proof_226.common import Report, load_proof_urls

NS_PER_SECOND = 1_000_000_000
MINUTE_NS = 60 * NS_PER_SECOND
STATEMENT_TIMEOUT = "60s"
WARM_RUNS = 3
#: TD3 bounds: Q1–Q4 execution warm, and planning (the chunk-interval rule).
EXECUTION_BOUND_MS = 1000.0
PLANNING_BOUND_MS = 50.0
BOUNDED = ("Q1", "Q2", "Q3", "Q4")
#: TD3 chunk-interval rule: chunks over the table's plausible span.
SPAN_YEARS = 20
CHUNK_RANGE = (1000, 2000)

_TARGET = """
SELECT ledger.instrument_id, ledger.session_date, ledger.record_count
     , ledger.first_event_ns, ledger.last_event_ns
  FROM tick_ingest_ledger AS ledger
  JOIN tick_archive_unit AS unit USING (unit_id)
  JOIN tick_request AS request USING (request_id)
 WHERE request.schema = %s AND unit.superseded_by_unit_id IS NULL
 ORDER BY ledger.record_count DESC, ledger.instrument_id
 LIMIT 1
"""
_SESSION_SPAN = """
SELECT min(first_event_ns), max(last_event_ns)
  FROM tick_ingest_ledger WHERE session_date = %s AND record_count > 0
"""


def _bars(lo: LiteralString, hi: LiteralString) -> LiteralString:
    """Minute bars for one instrument between two named bounds (Q2, Q5)."""
    return (
        "SELECT time_bucket(%(minute)s::bigint, ts_event) AS minute"
        "     , first(price, ts_event), max(price), min(price), last(price, ts_event)"
        "     , sum(size)"
        "  FROM tick_trade"
        f" WHERE instrument_id = %(id)s AND ts_event BETWEEN %({lo})s AND %({hi})s"
        " GROUP BY 1 ORDER BY 1"
    )


QUERIES: dict[str, LiteralString] = {
    "Q1": "SELECT * FROM tick_trade WHERE instrument_id = %(id)s"
    " AND ts_event BETWEEN %(lo)s AND %(hi)s"
    " ORDER BY ts_event, sequence, sequence_ordinal",
    "Q2": _bars("lo", "hi"),
    "Q3": "SELECT instrument_id, count(*) FROM tick_trade"
    " WHERE ts_event BETWEEN %(session_lo)s AND %(session_hi)s GROUP BY 1",
    "Q4": "SELECT * FROM tick_trade WHERE instrument_id = %(id)s"
    " ORDER BY ts_event DESC LIMIT 1",
    "Q5": _bars("month_lo", "month_hi"),
}


@dataclass(frozen=True)
class Target:
    tier: str
    instrument_id: int
    session_date: date
    records: int
    params: dict[str, int]


@dataclass(frozen=True)
class QueryResult:
    tier: str
    query: str
    planning_ms: float
    execution_ms: float
    hit_ratio: float
    rows: int

    @property
    def within_bounds(self) -> bool:
        return self.query not in BOUNDED or (
            self.execution_ms <= EXECUTION_BOUND_MS
            and self.planning_ms <= PLANNING_BOUND_MS
        )


def _ns(day: date) -> int:
    return (day - date(1970, 1, 1)).days * 86_400 * NS_PER_SECOND


def pick_targets(conn: psycopg.Connection[Any]) -> list[Target]:
    targets = []
    for tier in sorted(t.value for t in STORED_TIERS):
        row = conn.execute(_TARGET, (tier,)).fetchone()  # type: ignore[arg-type]
        if row is None:
            continue
        instrument, day, records, lo, hi = row
        span = conn.execute(_SESSION_SPAN, (day,)).fetchone()  # type: ignore[arg-type]
        assert span is not None
        month = day.replace(day=1)
        next_month = (month + timedelta(days=32)).replace(day=1)
        params = {
            "id": instrument,
            "minute": MINUTE_NS,
            "lo": lo,
            "hi": hi,
            "session_lo": span[0],
            "session_hi": span[1],
            "month_lo": _ns(month),
            "month_hi": _ns(next_month) - 1,
        }
        targets.append(Target(tier, int(instrument), day, int(records), params))
    return targets


def _explain(cur: psycopg.ClientCursor[Any], sql: str, params: dict[str, int]) -> Any:
    cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}", params)  # type: ignore[arg-type]
    row = cur.fetchone()
    assert row is not None
    plan = row[0]
    return (json.loads(plan) if isinstance(plan, str) else plan)[0]


def measure(
    cur: psycopg.ClientCursor[Any], tier: str, name: str, params: dict[str, int]
) -> QueryResult:
    """One query: a warm-up, then medians over ``WARM_RUNS``."""
    sql = QUERIES[name]
    _explain(cur, sql, params)
    runs = [_explain(cur, sql, params) for _ in range(WARM_RUNS)]
    top = runs[-1]["Plan"]
    hit = top.get("Shared Hit Blocks", 0)
    read = top.get("Shared Read Blocks", 0)
    return QueryResult(
        tier,
        name,
        median(r["Planning Time"] for r in runs),
        median(r["Execution Time"] for r in runs),
        hit / (hit + read) if hit + read else 1.0,
        int(top.get("Actual Rows", 0)),
    )


def run_query_set(
    conn: psycopg.Connection[Any], targets: list[Target]
) -> list[QueryResult]:
    """One row per (target, query), under ``STATEMENT_TIMEOUT``."""
    conn.execute(
        "SELECT set_config('statement_timeout', %s, false)", (STATEMENT_TIMEOUT,)
    )
    with psycopg.ClientCursor(conn) as cur:
        return [
            measure(cur, target.tier, name, target.params)
            for target in targets
            for name in QUERIES
        ]


def write_results(report: Report, results: list[QueryResult]) -> None:
    report.table(
        ("tier", "query", "planning ms", "execution ms", "buffer hit", "rows", "bound"),
        [
            (
                r.tier,
                r.query,
                f"{r.planning_ms:.2f}",
                f"{r.execution_ms:.1f}",
                f"{r.hit_ratio:.1%}",
                r.rows,
                "—"
                if r.query not in BOUNDED
                else ("ok" if r.within_bounds else "MISS"),
            )
            for r in results
        ],
    )


def projected_chunks() -> int:
    return round(timedelta(days=365.25 * SPAN_YEARS) / TICK_TRADE_CHUNK_INTERVAL)


def interval_verdict(results: list[QueryResult]) -> str:
    chunks = projected_chunks()
    worst = max((r.planning_ms for r in results if r.query in BOUNDED), default=0.0)
    in_range = CHUNK_RANGE[0] <= chunks <= CHUNK_RANGE[1]
    ok = in_range and worst <= PLANNING_BOUND_MS
    decision = "Validated" if ok else "Not validated: the go/no-go names a new interval"
    return (
        f"{TICK_TRADE_CHUNK_INTERVAL.days}-day chunks over {SPAN_YEARS} years: "
        f"{chunks} chunks ({'within' if in_range else 'outside'} "
        f"{CHUNK_RANGE[0]}–{CHUNK_RANGE[1]}); worst Q1–Q4 planning "
        f"{worst:.2f} ms (bound {PLANNING_BOUND_MS:.0f} ms). "
        f"**{decision}.**"
    )


def write_targets(report: Report, targets: list[Target]) -> None:
    report.table(
        ("tier", "instrument_id", "session", "records"),
        [(t.tier, t.instrument_id, t.session_date, f"{t.records:,}") for t in targets],
    )


def run() -> Path:
    urls = load_proof_urls()
    report = Report("queries", "Proof: query set, uncompressed baseline (slice 226)")
    with psycopg.connect(urls.db_url) as conn:
        targets = pick_targets(conn)
        chunks = conn.execute(
            "SELECT count(*) FROM show_chunks('tick_trade')"
        ).fetchone()
        results = run_query_set(conn, targets)
    report.add("## Targets (busiest instrument-session per tier)", "")
    write_targets(report, targets)
    report.add(f"## Uncompressed, {WARM_RUNS} warm runs (medians)", "")
    write_results(report, results)
    report.add(
        "## Chunk interval",
        "",
        f"Chunks held now: {chunks[0] if chunks else 'n/a'}.",
        "",
        interval_verdict(results),
    )
    return report.write()
