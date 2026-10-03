"""The proof harness's destructive-statement guard and the proof-database reset.

LLD 226 State Management: ``trading_tick_proof`` is disposable because the
provisioning script created it for this slice. Every harness function that
runs a destructive statement (TRUNCATE, DROP, DELETE, ALTER, decompress) is
declared with ``@destructive``: it first checks ``current_database()``
against ``TICK_PROOF_DB_NAME`` and refuses otherwise. The decorator also
registers the function in ``DESTRUCTIVE``, so a test can prove the guard on
every one.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, Concatenate, Protocol

from manta_trading.data.tick.constants import (
    STORED_TIERS,
    TICK_PROOF_DB_NAME,
    UnitState,
)


class SyncConn(Protocol):
    """The slice of ``psycopg.Connection`` the harness uses."""

    def execute(self, query: Any, params: Any = ..., /) -> Any: ...


class NotProofDatabaseError(RuntimeError):
    """A destructive harness statement was aimed at another database."""


#: Every guarded function, by name. Filled by ``@destructive``.
DESTRUCTIVE: dict[str, Callable[..., Any]] = {}


def require_proof_database(conn: SyncConn) -> None:
    """Raise unless ``conn`` is connected to ``TICK_PROOF_DB_NAME``."""
    (name,) = conn.execute("SELECT current_database()").fetchone()
    if name != TICK_PROOF_DB_NAME:
        raise NotProofDatabaseError(
            f"refusing a destructive statement on database {name!r}; "
            f"only {TICK_PROOF_DB_NAME!r} may be reset"
        )


def destructive[**P, R](
    func: Callable[Concatenate[SyncConn, P], R],
) -> Callable[Concatenate[SyncConn, P], R]:
    """Guard ``func`` (first argument: the connection) and register it."""

    @functools.wraps(func)
    def guarded(conn: SyncConn, *args: P.args, **kwargs: P.kwargs) -> R:
        require_proof_database(conn)
        return func(conn, *args, **kwargs)

    DESTRUCTIVE[func.__name__] = guarded
    return guarded


@destructive
def reset_proof_database(conn: SyncConn) -> int:
    """Empty the stored ticks and return every ingested tier unit to *verified*.

    The one place a unit state moves backward (LLD 226 TD2), so it lives here
    behind the guard, never in product code. Definition units stay
    *ingested*: ``tick_definition`` is not emptied. Returns the units reset.
    """
    conn.execute("TRUNCATE tick_trade, tick_ingest_ledger")
    cursor = conn.execute(
        """
        UPDATE tick_archive_unit AS unit
           SET state = %s
             , state_changed_at = now()
             , decoded_record_count = NULL
             , superseded_by_unit_id = NULL
          FROM tick_request AS request
         WHERE request.request_id = unit.request_id
           AND request.schema = ANY(%s)
           AND unit.state = %s
        """,
        (
            UnitState.VERIFIED.value,
            sorted(tier.value for tier in STORED_TIERS),
            UnitState.INGESTED.value,
        ),
    )
    return int(cursor.rowcount)
