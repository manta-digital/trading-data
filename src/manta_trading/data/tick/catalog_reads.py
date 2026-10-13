"""A product's contract definitions and the sessions held for each (slice 231).

The catalog read behind ``/api/v1/futures/products/{product}/contracts``. One
row per instrument whose definition names the product, spreads included,
joined to the first and last session holding its records under current units.
One statement; it reads ``tick_definition`` and the ledger, never
``tick_trade``. No API import, so the CLI can use it later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from manta_trading.data.tick.manifest_reads import COVERAGE_PREDICATE, Conn

#: Per instrument: the validity span over all its definition rows, and the
#: attributes of its latest row (a re-issued definition supersedes the old).
_CONTRACTS = (
    "WITH latest AS ("
    " SELECT DISTINCT ON (instrument_id) instrument_id, raw_symbol,"
    " instrument_class, exchange, currency, min_price_increment,"
    " contract_multiplier, unit_of_measure, unit_of_measure_qty"
    " FROM tick_definition WHERE asset = %(product)s"
    " ORDER BY instrument_id, activation_ns DESC),"
    " span AS ("
    " SELECT instrument_id, min(activation_ns) AS activation_ns,"
    " max(expiration_ns) AS expiration_ns"
    " FROM tick_definition WHERE asset = %(product)s GROUP BY instrument_id),"
    " held AS ("
    " SELECT l.instrument_id, min(l.session_date) AS first_session,"
    " max(l.session_date) AS last_session"
    " FROM tick_ingest_ledger l JOIN tick_archive_unit u USING (unit_id)"
    " WHERE l.record_count > 0 AND l.instrument_id IN"
    " (SELECT instrument_id FROM span)"
    f" AND {COVERAGE_PREDICATE}"
    " GROUP BY l.instrument_id)"
    " SELECT s.instrument_id, d.raw_symbol, d.instrument_class,"
    " s.activation_ns, s.expiration_ns, d.exchange, d.currency,"
    " d.min_price_increment, d.contract_multiplier, d.unit_of_measure,"
    " d.unit_of_measure_qty, h.first_session, h.last_session"
    " FROM span s JOIN latest d USING (instrument_id)"
    " LEFT JOIN held h USING (instrument_id)"
    " ORDER BY s.expiration_ns, s.instrument_id"
)


@dataclass(frozen=True)
class CatalogContract:
    """One instrument's definition, as stored (prices and times unformatted)."""

    instrument_id: int
    raw_symbol: str | None
    instrument_class: str | None
    activation_ns: int
    expiration_ns: int
    exchange: str | None
    currency: str | None
    min_price_increment: int | None  # fixed-point, TICK_PRICE_SCALE
    contract_multiplier: int | None
    unit_of_measure: str | None
    unit_of_measure_qty: int | None  # fixed-point, TICK_PRICE_SCALE
    #: First and last session holding records; ``None`` when nothing is held.
    held: tuple[date, date] | None


async def contracts_for(conn: Conn, product: str) -> list[CatalogContract]:
    """Every instrument defined for ``product``, by expiration then id."""
    cursor = await conn.execute(_CONTRACTS, {"product": product})
    return [_contract(row) for row in await cursor.fetchall()]


def _contract(row: tuple[Any, ...]) -> CatalogContract:
    (iid, symbol, klass, activation, expiration, exchange, currency) = row[:7]
    (increment, multiplier, unit, unit_qty, first, last) = row[7:]
    return CatalogContract(
        instrument_id=iid,
        raw_symbol=symbol,
        instrument_class=klass,
        activation_ns=activation,
        expiration_ns=expiration,
        exchange=exchange,
        currency=currency,
        min_price_increment=increment,
        contract_multiplier=multiplier,
        unit_of_measure=unit,
        unit_of_measure_qty=unit_qty,
        held=None if first is None else (first, last),
    )
