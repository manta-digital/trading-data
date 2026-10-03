"""``final``: the production tick database after its rebuild (LLD 226 TD2).

On ``trading_tick``: compress every chunk the policy would (older than
``TICK_TRADE_COMPRESS_AFTER``; the policy's own work done early), then the
query set on the final layout, ``coverage`` over each tier's range, and table
sizes. Read-only apart from the compression, which runs only after the
connection's database is checked against the one ``MT_TICK_DB_URL`` names.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psycopg
from dotenv import dotenv_values

from manta_trading.data.tick.constants import TICK_TRADE_COMPRESS_AFTER
from manta_trading.market.schema.migrations.tick import interval_to_ns
from proof_226 import queries
from proof_226.cli import TICK_DB_URL_ENV, TICK_MAINTENANCE_URL_ENV, run_mt
from proof_226.common import (
    ENV_FILE,
    EXPECTED_ROWS,
    ProofSetupError,
    ProofUrls,
    Report,
    require_free_space,
)
from proof_226.guard import SyncConn
from proof_226.rebuild import PARTIAL_ACCEPT, tier_ranges

_COMPRESS = (
    "SELECT count(compress_chunk(c, if_not_compressed => true))"
    " FROM show_chunks('tick_trade', older_than => tick_now_ns() - %s) AS c"
)
_SIZES = """
SELECT count(*) FILTER (WHERE compression_status = 'Compressed')
     , count(*)
     , sum(before_compression_total_bytes)
     , sum(after_compression_total_bytes)
  FROM chunk_columnstore_stats('tick_trade')
"""


class NotProductionTickDatabaseError(RuntimeError):
    """``final``'s compression was aimed at a database ``MT_TICK_DB_URL`` does
    not name."""


def tick_urls(env_file: Path = ENV_FILE) -> ProofUrls:
    """The production tick database's two URLs (the harness's URL pair)."""
    values = dotenv_values(env_file)
    db, maintenance = values.get(TICK_DB_URL_ENV), values.get(TICK_MAINTENANCE_URL_ENV)
    if not db or not maintenance:
        raise ProofSetupError(f"{env_file} lacks the tick URLs")
    return ProofUrls(db_url=db, maintenance_url=maintenance)


def database_named(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def require_tick_database(conn: SyncConn, expected: str) -> None:
    (name,) = conn.execute("SELECT current_database()").fetchone()
    if name != expected:
        raise NotProductionTickDatabaseError(
            f"refusing to compress database {name!r}; MT_TICK_DB_URL names {expected!r}"
        )


def compress_eligible(conn: SyncConn, expected: str) -> int:
    """Compress every chunk older than the policy age; return how many."""
    require_free_space()
    require_tick_database(conn, expected)
    (count,) = conn.execute(
        _COMPRESS, (interval_to_ns(TICK_TRADE_COMPRESS_AFTER),)
    ).fetchone()
    return int(count)


def _sizes(conn: psycopg.Connection[Any]) -> tuple[int, int, int, int, int]:
    compressed, chunks, before, after = conn.execute(_SIZES).fetchone()  # type: ignore[misc]
    (rows,) = conn.execute("SELECT count(*) FROM tick_trade").fetchone()  # type: ignore[misc]
    return int(compressed), int(chunks), int(before), int(after), int(rows)


def run() -> Path:
    urls = tick_urls()
    expected = database_named(urls.db_url)
    report = Report("final", "Proof: the production tick database (slice 226)")
    with psycopg.connect(urls.maintenance_url, autocommit=True) as conn:
        newly = compress_eligible(conn, expected)
    with psycopg.connect(urls.db_url) as conn:
        compressed, chunks, before, after, rows = _sizes(conn)
        targets = queries.pick_targets(conn)
        results = queries.run_query_set(conn, targets)
    report.add(
        "## Storage",
        "",
        f"- chunks compressed by this run: {newly}; compressed now: "
        f"{compressed} of {chunks}",
        f"- tick_trade rows (exact): {rows:,} (expected {EXPECTED_ROWS:,}: "
        f"{'ok' if rows == EXPECTED_ROWS else 'MISS'})",
        f"- before compression: {before / 1024**3:.2f} GiB; after: "
        f"{after / 1024**3:.2f} GiB; {after / rows:.2f} B/row",
        "",
        "## Query set on the final layout (medians of 3 warm runs)",
        "",
    )
    queries.write_targets(report, targets)
    queries.write_results(report, results)
    report.add("## Coverage", "")
    for tier, start, end in tier_ranges(urls):
        cov = run_mt(
            urls,
            "data",
            "tick",
            "coverage",
            "--start",
            start,
            "--end",
            end,
            accept=PARTIAL_ACCEPT,
        )
        report.add(
            f"- {tier} {start} → {end}: exit {cov.exit_code} "
            f"({'ok' if cov.exit_code == 0 else 'MISMATCH'})"
        )
    return report.write()
