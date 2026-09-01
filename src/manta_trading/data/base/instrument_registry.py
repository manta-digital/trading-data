"""
Instrument Registry for managing tradeable instruments and provider mappings.

Provides InstrumentRegistry backed by TimescaleDB via psycopg3. Uses a
per-instance dict cache (not lru_cache) to avoid cross-instance pollution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from manta_trading.logging import get_logger

_logger = get_logger(__name__)


@dataclass
class Instrument:
    """
    Represents a tradeable instrument with metadata.

    Attributes:
        instrument_id: Primary key from database
        canonical_id: Unique canonical identifier (e.g., "AAPL.NASDAQ")
        symbol: Primary trading symbol
        asset_class: Type of asset ('equity', 'etf', 'future', 'crypto', 'option')
        venue: Trading venue ('NASDAQ', 'NYSE', 'CME', etc.)
        currency: Currency code (default: USD)
        tick_size: Minimum price increment
        lot_size: Contract/share multiplier
        trading_calendar_id: Reference to trading calendar
        adjustment_policy: Default adjustment policy for this instrument
        metadata: Additional properties as JSON
    """

    instrument_id: int
    canonical_id: str
    symbol: str
    asset_class: str
    venue: str
    currency: str = "USD"
    tick_size: float | None = None
    lot_size: int = 1
    trading_calendar_id: str | None = None
    adjustment_policy: str = "split_adjusted"
    metadata: dict | None = None
    # Lifecycle columns added in slice 141 (migration 015)
    first_listing_date: date | None = None
    first_data_date: date | None = None
    delisted_date: date | None = None
    eodhd_type: str | None = None
    eodhd_exchange: str | None = None
    delisted_at_eodhd: bool = False


# Columns selected in all instrument queries (single source of truth)
_INSTRUMENT_COLS = (
    "instrument_id, canonical_id, symbol, asset_class, venue, "
    "currency, tick_size, lot_size, trading_calendar_id, "
    "adjustment_policy, metadata, "
    "first_listing_date, first_data_date, delisted_date, "
    "eodhd_type, eodhd_exchange, delisted_at_eodhd"
)


class InstrumentRegistry:
    """
    Manages instrument registration and lookup against TimescaleDB.

    Uses psycopg3 ConnectionPool for connection management and a per-instance
    dict for caching. Cache is invalidated on any write operation.
    """

    def __init__(self, conninfo: str) -> None:
        """
        Initialize the instrument registry.

        Args:
            conninfo: PostgreSQL connection string for TimescaleDB
        """
        self._pool = ConnectionPool(conninfo, min_size=1, max_size=5)
        self._cache: dict[str, Instrument] = {}
        _logger.info("InstrumentRegistry initialized")

    def close(self) -> None:
        """Close the connection pool."""
        self._pool.close()

    def _invalidate_cache(self) -> None:
        """Clear the per-instance cache."""
        self._cache.clear()

    def _row_to_instrument(self, row: dict) -> Instrument:
        """Map a DB row dict to an Instrument dataclass."""
        return Instrument(
            instrument_id=row["instrument_id"],
            canonical_id=row["canonical_id"],
            symbol=row["symbol"],
            asset_class=row["asset_class"],
            venue=row["venue"],
            currency=row["currency"],
            tick_size=row["tick_size"],
            lot_size=row["lot_size"],
            trading_calendar_id=row["trading_calendar_id"],
            adjustment_policy=row["adjustment_policy"],
            metadata=row["metadata"],
            first_listing_date=row.get("first_listing_date"),
            first_data_date=row.get("first_data_date"),
            delisted_date=row.get("delisted_date"),
            eodhd_type=row.get("eodhd_type"),
            eodhd_exchange=row.get("eodhd_exchange"),
            delisted_at_eodhd=row.get("delisted_at_eodhd", False),
        )

    # ------------------------------------------------------------------
    # Lookup methods (cached)
    # ------------------------------------------------------------------

    def get_instrument(self, symbol: str) -> Instrument | None:
        """Look up the first active instrument matching this symbol.

        Alias for get_by_symbol — satisfies the DataProcessor.classify_sessions
        interface which calls registry.get_instrument(symbol).

        Args:
            symbol: Trading symbol (e.g., "AAPL")

        Returns:
            Instrument if found, None otherwise.
        """
        return self.get_by_symbol(symbol)

    def get_by_symbol(self, symbol: str) -> Instrument | None:
        """
        Look up the first active instrument matching this symbol.

        Args:
            symbol: Trading symbol (e.g., "AAPL")

        Returns:
            Instrument if found, None otherwise. Note: symbol is not unique —
            multiple venues may list the same symbol. Use get_by_canonical_id
            for an unambiguous lookup.
        """
        key = f"symbol:{symbol}"
        if key in self._cache:
            return self._cache[key]

        sql = (
            f"SELECT {_INSTRUMENT_COLS} FROM instruments "
            "WHERE symbol = %s AND delisted_at_eodhd = FALSE AND delisted_date IS NULL LIMIT 1"
        )
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (symbol,))
                row = cur.fetchone()

        if row is None:
            return None

        inst = self._row_to_instrument(row)
        self._cache[key] = inst
        return inst

    def get_by_canonical_id(self, canonical_id: str) -> Instrument | None:
        """
        Look up an instrument by its canonical ID.

        Args:
            canonical_id: Canonical identifier (e.g., "AAPL.NASDAQ")

        Returns:
            Instrument if found, None otherwise
        """
        key = f"canonical:{canonical_id}"
        if key in self._cache:
            return self._cache[key]

        sql = f"SELECT {_INSTRUMENT_COLS} FROM instruments WHERE canonical_id = %s"
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (canonical_id,))
                row = cur.fetchone()

        if row is None:
            return None

        inst = self._row_to_instrument(row)
        self._cache[key] = inst
        return inst

    def get_by_provider_symbol(
        self,
        provider: str,
        provider_symbol: str,
        as_of_date: date | None = None,
    ) -> Instrument | None:
        """
        Look up an instrument by provider-specific symbol.

        Args:
            provider: Provider name (e.g., 'alphavantage')
            provider_symbol: Provider-specific symbol
            as_of_date: Date for historical mapping lookup (default: today)

        Returns:
            Instrument if found, None otherwise
        """
        lookup_date = as_of_date if as_of_date is not None else date.today()
        key = f"provider:{provider}:{provider_symbol}:{lookup_date}"
        if key in self._cache:
            return self._cache[key]

        # Prefix each column with 'i.' for the JOIN query
        i_cols = ", ".join(f"i.{col.strip()}" for col in _INSTRUMENT_COLS.split(","))
        sql = (
            f"SELECT {i_cols} "
            "FROM instruments i "
            "JOIN provider_symbol_mapping psm ON i.instrument_id = psm.instrument_id "
            "WHERE psm.provider = %s AND psm.provider_symbol = %s "
            "  AND psm.valid_from <= %s "
            "  AND (psm.valid_to IS NULL OR psm.valid_to > %s)"
        )
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (provider, provider_symbol, lookup_date, lookup_date))
                row = cur.fetchone()

        if row is None:
            return None

        inst = self._row_to_instrument(row)
        self._cache[key] = inst
        return inst

    # ------------------------------------------------------------------
    # Write methods (invalidate cache)
    # ------------------------------------------------------------------

    def register_instrument(
        self,
        canonical_id: str,
        symbol: str,
        asset_class: str,
        venue: str,
        currency: str = "USD",
        tick_size: float | None = None,
        lot_size: int = 1,
        trading_calendar_id: str | None = None,
        adjustment_policy: str = "split_adjusted",
        metadata: dict | None = None,
    ) -> tuple[Instrument, bool]:
        """
        Register a new instrument, returning existing on conflict.

        Uses ON CONFLICT (canonical_id) DO NOTHING. If the canonical_id already
        exists, fetches and returns the existing record.

        Returns:
            Tuple of (Instrument, was_inserted) where was_inserted is True if a
            new row was written, False if the canonical_id already existed.
        """
        sql = (
            f"INSERT INTO instruments "
            "(canonical_id, symbol, asset_class, venue, currency, tick_size, "
            "lot_size, trading_calendar_id, adjustment_policy, metadata) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (canonical_id) DO NOTHING "
            f"RETURNING {_INSTRUMENT_COLS}"
        )
        params = (
            canonical_id,
            symbol,
            asset_class,
            venue,
            currency,
            tick_size,
            lot_size,
            trading_calendar_id,
            adjustment_policy,
            metadata,
        )

        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                row = cur.fetchone()

        self._invalidate_cache()

        if row is not None:
            return self._row_to_instrument(row), True

        # Conflict triggered — fetch existing
        existing = self.get_by_canonical_id(canonical_id)
        if existing is None:
            raise RuntimeError(
                f"register_instrument: no row returned and get_by_canonical_id "
                f"returned None for '{canonical_id}'"
            )
        return existing, False

    def update_provider_mapping(
        self,
        instrument_id: int,
        provider: str,
        provider_symbol: str,
    ) -> bool:
        """
        Create a provider symbol mapping, ignoring duplicates.

        Args:
            instrument_id: FK to instruments table
            provider: Provider name (e.g., 'alphavantage')
            provider_symbol: Provider-specific symbol

        Returns:
            True if a new mapping was inserted, False if it already existed.
        """
        sql = (
            "INSERT INTO provider_symbol_mapping (instrument_id, provider, provider_symbol) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING"
        )
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (instrument_id, provider, provider_symbol))
                inserted = cur.rowcount > 0

        self._invalidate_cache()
        return inserted

    def upsert_eodhd_universe(self, symbols: list[dict]) -> tuple[int, int, int]:
        """Idempotently upsert EODHD bulk-list rows into instruments.

        For existing rows (matched by canonical_id), only EODHD-sourced fields
        are updated: eodhd_type, eodhd_exchange, delisted_at_eodhd, currency.
        venue, trading_calendar_id, and canonical_id are NEVER overwritten.

        Args:
            symbols: List of dicts with keys: canonical_id, symbol, asset_class,
                venue, currency, trading_calendar_id, eodhd_type, eodhd_exchange,
                delisted_at_eodhd.

        Returns:
            Tuple of (inserted, updated, unchanged).
        """
        if not symbols:
            return (0, 0, 0)

        # RETURNING (xmax = 0) distinguishes insert from update.
        # When the DO UPDATE WHERE clause is false (row unchanged), no row is
        # returned — counted as unchanged.
        sql = """
            INSERT INTO instruments (
                canonical_id, symbol, asset_class, venue, currency,
                trading_calendar_id, eodhd_type, eodhd_exchange, delisted_at_eodhd
            ) VALUES (
                %(canonical_id)s, %(symbol)s, %(asset_class)s, %(venue)s,
                %(currency)s, %(trading_calendar_id)s, %(eodhd_type)s,
                %(eodhd_exchange)s, %(delisted_at_eodhd)s
            )
            ON CONFLICT (canonical_id) DO UPDATE SET
                eodhd_type        = EXCLUDED.eodhd_type,
                eodhd_exchange    = EXCLUDED.eodhd_exchange,
                delisted_at_eodhd = EXCLUDED.delisted_at_eodhd,
                currency          = EXCLUDED.currency,
                updated_at        = NOW()
            WHERE (
                instruments.eodhd_type        IS DISTINCT FROM EXCLUDED.eodhd_type
                OR instruments.eodhd_exchange  IS DISTINCT FROM EXCLUDED.eodhd_exchange
                OR instruments.delisted_at_eodhd IS DISTINCT FROM EXCLUDED.delisted_at_eodhd
                OR instruments.currency        IS DISTINCT FROM EXCLUDED.currency
            )
            RETURNING (xmax = 0) AS was_inserted
        """
        inserted = updated = unchanged = 0
        with self._pool.connection() as conn:
            for row in symbols:
                with conn.cursor() as cur:
                    cur.execute(sql, row)
                    result = cur.fetchone()
                    if result is None:
                        unchanged += 1
                    elif result[0]:
                        inserted += 1
                    else:
                        updated += 1

        self._invalidate_cache()
        return (inserted, updated, unchanged)

    def list_instruments(
        self,
        *,
        asset_class: str | None = None,
        venue: str | None = None,
        active_only: bool = True,
    ) -> list[Instrument]:
        """
        List instruments with optional filtering.

        Args:
            asset_class: Filter by asset class (None = all)
            venue: Filter by venue (None = all)
            active_only: If True, return only active instruments

        Returns:
            List of Instrument dataclasses ordered by symbol, venue
        """
        sql = (
            f"SELECT {_INSTRUMENT_COLS} FROM instruments "
            "WHERE (%s::text IS NULL OR asset_class = %s) "
            "  AND (%s::text IS NULL OR venue = %s) "
            "  AND (NOT %s OR (delisted_at_eodhd = FALSE AND delisted_date IS NULL)) "
            "ORDER BY symbol, venue"
        )
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (asset_class, asset_class, venue, venue, active_only))
                rows = cur.fetchall()

        return [self._row_to_instrument(row) for row in rows]
