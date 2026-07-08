# Foundation API Usage Examples

Complete examples for using the Slice 750 foundation modules in your trading applications.

## Table of Contents
- [Setup](#setup)
- [InstrumentRegistry Examples](#instrumentregistry-examples)
- [TradingCalendar Examples](#tradingcalendar-examples)
- [SessionClassifier Examples](#sessionclassifier-examples)
- [AdjustmentPolicy Examples](#adjustmentpolicy-examples)
- [Complete Workflows](#complete-workflows)

---

## Setup

### Database Configuration

```python
import os

# Database configuration from environment
db_config = {
    'host': os.getenv('TRADING_PSQL_HOST'),
    'port': int(os.getenv('TRADING_PSQL_PORT', 5432)),
    'database': os.getenv('TRADING_PSQL_DB'),
    'user': os.getenv('TRADING_PSQL_USER'),
    'password': os.getenv('TRADING_PSQL_PASSWORD')
}
```

### Required Imports

```python
from datetime import date, datetime, timezone
import pandas as pd
import pytz

from manta_trading.data.base.instrument_registry import InstrumentRegistry
from manta_trading.data.base.trading_calendar import TradingCalendar
from manta_trading.data.base.session_classifier import (
    classify_bar_session,
    split_bars_by_session,
    add_session_column
)
from manta_trading.data.base.adjustment_policy import (
    AdjustmentPolicy,
    SessionType,
    DataVersion,
    validate_ohlcv_consistency
)
```

---

## InstrumentRegistry Examples

### Example 1: Register a New Instrument

```python
from manta_trading.data.base.instrument_registry import InstrumentRegistry

# Initialize registry
registry = InstrumentRegistry(db_config)

try:
    # Register a new stock
    instrument = registry.register_instrument(
        canonical_id='NVDA.NASDAQ',
        symbol='NVDA',
        asset_class='stock',
        venue='NASDAQ',
        currency='USD',
        tick_size=0.01,
        lot_size=1,
        trading_calendar_id='NASDAQ',
        adjustment_policy='split_adjusted',
        active=True,
        metadata={'sector': 'Technology', 'industry': 'Semiconductors'}
    )

    print(f"Registered: {instrument.canonical_id}")
    print(f"Instrument ID: {instrument.instrument_id}")

finally:
    registry.close()
```

### Example 2: Look Up Instrument by Canonical ID

```python
registry = InstrumentRegistry(db_config)

try:
    # Look up AAPL
    aapl = registry.get_instrument_by_canonical_id('AAPL.NASDAQ')

    if aapl:
        print(f"Symbol: {aapl.symbol}")
        print(f"Venue: {aapl.venue}")
        print(f"Calendar: {aapl.trading_calendar_id}")
        print(f"Active: {aapl.active}")
        print(f"Metadata: {aapl.metadata}")
    else:
        print("Instrument not found")

finally:
    registry.close()
```

### Example 3: Look Up by Provider Symbol

```python
registry = InstrumentRegistry(db_config)

try:
    # Look up using AlphaVantage symbol
    instrument = registry.get_instrument_by_provider_symbol(
        provider='alphavantage',
        provider_symbol='MSFT'
    )

    if instrument:
        print(f"Canonical ID: {instrument.canonical_id}")
        print(f"Symbol: {instrument.symbol}")

    # Historical lookup (what was FB before it became META?)
    old_fb = registry.get_instrument_by_provider_symbol(
        provider='alphavantage',
        provider_symbol='FB',
        as_of_date=date(2022, 1, 1)  # Before ticker change
    )

    if old_fb:
        print(f"FB in 2022: {old_fb.canonical_id}")

finally:
    registry.close()
```

### Example 4: Update Provider Mapping

```python
registry = InstrumentRegistry(db_config)

try:
    # Symbol change: FB → META on June 9, 2022
    registry.update_provider_mapping(
        canonical_id='META.NASDAQ',
        provider='alphavantage',
        provider_symbol='META',
        valid_from=date(2022, 6, 9),
        metadata={'reason': 'Ticker symbol change from FB'}
    )

    print("Provider mapping updated")

    # The old FB mapping will automatically have valid_to set to 2022-06-09

finally:
    registry.close()
```

### Example 5: List Instruments with Filters

```python
registry = InstrumentRegistry(db_config)

try:
    # Get all NASDAQ stocks
    nasdaq_stocks = registry.list_instruments(
        asset_class='stock',
        venue='NASDAQ',
        active=True
    )

    print(f"Found {len(nasdaq_stocks)} NASDAQ stocks")

    for stock in nasdaq_stocks[:5]:  # First 5
        print(f"  {stock.symbol}: {stock.canonical_id}")

    # Get all NYSE stocks
    nyse_stocks = registry.list_instruments(
        asset_class='stock',
        venue='NYSE'
    )

    print(f"Found {len(nyse_stocks)} NYSE stocks")

finally:
    registry.close()
```

---

## TradingCalendar Examples

### Example 1: Check Trading Days

```python
from manta_trading.data.base.trading_calendar import TradingCalendar

# Initialize calendar for NYSE
calendar = TradingCalendar('NYSE', db_config)

try:
    # Check if specific dates are trading days
    dates_to_check = [
        date(2024, 1, 1),   # New Year's Day (holiday)
        date(2024, 1, 2),   # Tuesday (trading day)
        date(2024, 1, 6),   # Saturday (weekend)
        date(2024, 11, 29), # Day after Thanksgiving (early close, still trading)
    ]

    for check_date in dates_to_check:
        is_trading = calendar.is_trading_day(check_date)
        print(f"{check_date}: {'TRADING' if is_trading else 'CLOSED'}")

finally:
    calendar.close()
```

### Example 2: Get Trading Hours

```python
calendar = TradingCalendar('NYSE', db_config)

try:
    # Regular trading day
    regular_day = date(2024, 1, 2)
    rth_hours = calendar.get_trading_hours(regular_day, SessionType.RTH)

    if rth_hours:
        print(f"RTH: {rth_hours.session_start} to {rth_hours.session_end}")
        # Output: RTH: 2024-01-02 09:30:00-05:00 to 2024-01-02 16:00:00-05:00

    # Extended hours
    eth_hours = calendar.get_trading_hours(regular_day, SessionType.ETH)

    if eth_hours:
        print(f"ETH: {eth_hours.session_start} to {eth_hours.session_end}")
        # Output: ETH: 2024-01-02 04:00:00-05:00 to 2024-01-02 20:00:00-05:00

    # Early close day (day after Thanksgiving)
    early_close = date(2024, 11, 29)
    hours = calendar.get_trading_hours(early_close, SessionType.RTH)

    if hours:
        print(f"Early close: {hours.session_start} to {hours.session_end}")
        # Output: Early close: 2024-11-29 09:30:00-05:00 to 2024-11-29 13:00:00-05:00

finally:
    calendar.close()
```

### Example 3: Get Holidays for a Year

```python
calendar = TradingCalendar('NYSE', db_config)

try:
    # Get all 2024 holidays
    holidays_2024 = calendar.get_holidays(2024)

    print(f"NYSE has {len(holidays_2024)} holidays in 2024:")

    for holiday in holidays_2024:
        status = holiday.market_status
        extra = f" (closes {holiday.early_close_time})" if holiday.early_close_time else ""
        print(f"  {holiday.holiday_date}: {holiday.holiday_name} - {status}{extra}")

finally:
    calendar.close()
```

### Example 4: Calculate Expected Bar Count

```python
calendar = TradingCalendar('NYSE', db_config)

try:
    # How many 1-minute bars in a single trading day?
    single_day_bars = calendar.get_expected_bar_count(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 2),
        timeframe_minutes=1,
        session_type=SessionType.RTH
    )

    print(f"Bars in one RTH day: {single_day_bars}")
    # Output: 390 bars (6.5 hours * 60 minutes)

    # How many bars in a trading week?
    week_bars = calendar.get_expected_bar_count(
        start_date=date(2024, 1, 2),  # Tuesday
        end_date=date(2024, 1, 5),    # Friday
        timeframe_minutes=1,
        session_type=SessionType.RTH
    )

    print(f"Bars in a week: {week_bars}")
    # Output: 1560 bars (4 days * 390)

    # 5-minute bars
    bars_5min = calendar.get_expected_bar_count(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 2),
        timeframe_minutes=5,
        session_type=SessionType.RTH
    )

    print(f"5-minute bars in one day: {bars_5min}")
    # Output: 78 bars

finally:
    calendar.close()
```

---

## SessionClassifier Examples

### Example 1: Classify Individual Bars

```python
from manta_trading.data.base.session_classifier import classify_bar_session
import pytz

# Create calendar
calendar = TradingCalendar('NYSE', db_config)

try:
    tz = pytz.timezone('America/New_York')

    # Classify timestamps
    timestamps = [
        tz.localize(datetime(2024, 1, 2, 10, 0)),   # 10:00 AM
        tz.localize(datetime(2024, 1, 2, 7, 0)),    # 7:00 AM (pre-market)
        tz.localize(datetime(2024, 1, 2, 18, 0)),   # 6:00 PM (after-hours)
        tz.localize(datetime(2024, 1, 6, 10, 0)),   # Saturday
    ]

    for ts in timestamps:
        session = classify_bar_session(ts, calendar)
        print(f"{ts}: {session.value}")
        # Output:
        # 2024-01-02 10:00:00-05:00: RTH
        # 2024-01-02 07:00:00-05:00: ETH
        # 2024-01-02 18:00:00-05:00: ETH
        # 2024-01-06 10:00:00-05:00: ETH (weekend)

finally:
    calendar.close()
```

### Example 2: Split DataFrame by Session

```python
from manta_trading.data.base.session_classifier import split_bars_by_session
import pandas as pd

calendar = TradingCalendar('NYSE', db_config)

try:
    # Sample bar data
    tz = pytz.timezone('America/New_York')
    df = pd.DataFrame({
        'timestamp': [
            tz.localize(datetime(2024, 1, 2, 10, 0)),
            tz.localize(datetime(2024, 1, 2, 11, 0)),
            tz.localize(datetime(2024, 1, 2, 7, 0)),
            tz.localize(datetime(2024, 1, 2, 18, 0)),
        ],
        'open': [100.0, 101.0, 99.5, 100.5],
        'high': [100.5, 101.5, 100.0, 101.0],
        'low': [99.5, 100.5, 99.0, 100.0],
        'close': [101.0, 101.2, 100.0, 100.8],
        'volume': [1000, 1200, 500, 300]
    })

    # Split by session
    sessions = split_bars_by_session(df, calendar)

    print(f"RTH bars: {len(sessions[SessionType.RTH])}")
    print(f"ETH bars: {len(sessions[SessionType.ETH])}")

    # Process RTH data separately
    rth_df = sessions[SessionType.RTH]
    print(f"\nRTH bars:\n{rth_df}")

    # Process ETH data separately
    eth_df = sessions[SessionType.ETH]
    print(f"\nETH bars:\n{eth_df}")

finally:
    calendar.close()
```

### Example 3: Add Session Column

```python
from manta_trading.data.base.session_classifier import add_session_column

calendar = TradingCalendar('NYSE', db_config)

try:
    # Sample data
    tz = pytz.timezone('America/New_York')
    df = pd.DataFrame({
        'timestamp': [
            tz.localize(datetime(2024, 1, 2, 10, 0)),
            tz.localize(datetime(2024, 1, 2, 14, 30)),
            tz.localize(datetime(2024, 1, 2, 7, 0)),
        ],
        'price': [100.0, 101.0, 99.5]
    })

    # Add session_type column
    df_with_sessions = add_session_column(df, calendar)

    print(df_with_sessions)
    # Output:
    #                  timestamp  price session_type
    # 0 2024-01-02 10:00:00-05:00  100.0         RTH
    # 1 2024-01-02 14:30:00-05:00  101.0         RTH
    # 2 2024-01-02 07:00:00-05:00   99.5         ETH

    # Now you can filter or group by session
    rth_avg = df_with_sessions[df_with_sessions['session_type'] == 'RTH']['price'].mean()
    print(f"RTH average price: {rth_avg}")

finally:
    calendar.close()
```

---

## AdjustmentPolicy Examples

### Example 1: Validate OHLCV Data

```python
from manta_trading.data.base.adjustment_policy import validate_ohlcv_consistency

# Valid bar
result = validate_ohlcv_consistency(
    open_price=100.0,
    high=105.0,
    low=99.0,
    close=103.0
)

print(f"Valid: {result.is_valid}")
print(f"Errors: {result.errors}")
# Output: Valid: True, Errors: []

# Invalid bar (high too low)
result = validate_ohlcv_consistency(
    open_price=100.0,
    high=102.0,  # Lower than close!
    low=99.0,
    close=105.0
)

print(f"Valid: {result.is_valid}")
print(f"Errors: {result.errors}")
# Output: Valid: False, Errors: ['high (102.0) must be >= max(open, close) (105.0)']

# Check for negative prices
result = validate_ohlcv_consistency(
    open_price=-100.0,
    high=-99.0,
    low=-101.0,
    close=-100.0
)

print(f"Valid: {result.is_valid}")
print(f"Errors: {result.errors}")
# Output: Valid: False, Errors: ['open cannot be negative: -100.0', ...]
```

### Example 2: Use DataVersion for Tracking

```python
from manta_trading.data.base.adjustment_policy import DataVersion

# Create version metadata
version = DataVersion(
    version="1.0.0",
    ingestion_timestamp=datetime.now(timezone.utc),
    provider_version="alphavantage_api_v2.3"
)

print(f"Data version: {version.version}")
print(f"Ingested at: {version.ingestion_timestamp}")
print(f"Provider: {version.provider_version}")

# Store in database
# INSERT INTO minute_ohlcv (..., data_version, ingestion_timestamp, provider_version)
# VALUES (..., %s, %s, %s), (version.version, version.ingestion_timestamp, version.provider_version)
```

---

## Complete Workflows

### Workflow 1: Validate and Store Bar Data

```python
def store_minute_bar(symbol, timestamp, open, high, low, close, volume, db_config):
    """
    Complete workflow: validate bar, classify session, store to database.
    """
    # Step 1: Look up instrument
    registry = InstrumentRegistry(db_config)
    instrument = registry.get_instrument_by_provider_symbol('alphavantage', symbol)

    if not instrument:
        raise ValueError(f"Unknown symbol: {symbol}")

    # Step 2: Validate OHLCV
    validation = validate_ohlcv_consistency(open, high, low, close)
    if not validation.is_valid:
        raise ValueError(f"Invalid OHLCV: {validation.errors}")

    # Step 3: Classify session
    calendar = TradingCalendar(instrument.trading_calendar_id, db_config)
    session_type = classify_bar_session(timestamp, calendar)

    # Step 4: Store to database
    # ... INSERT INTO minute_ohlcv ...

    registry.close()
    calendar.close()

    return {
        'canonical_id': instrument.canonical_id,
        'session_type': session_type.value,
        'validated': True
    }
```

### Workflow 2: Process Historical Data with Sessions

```python
def process_historical_data(symbol, df, db_config):
    """
    Process historical DataFrame: add sessions, split by RTH/ETH, compute stats.
    """
    # Get instrument
    registry = InstrumentRegistry(db_config)
    instrument = registry.get_instrument_by_provider_symbol('alphavantage', symbol)

    # Get calendar
    calendar = TradingCalendar(instrument.trading_calendar_id, db_config)

    # Add session classification
    df_with_sessions = add_session_column(df, calendar)

    # Compute separate stats for RTH and ETH
    rth_data = df_with_sessions[df_with_sessions['session_type'] == 'RTH']
    eth_data = df_with_sessions[df_with_sessions['session_type'] == 'ETH']

    stats = {
        'rth_bars': len(rth_data),
        'rth_avg_volume': rth_data['volume'].mean() if len(rth_data) > 0 else 0,
        'eth_bars': len(eth_data),
        'eth_avg_volume': eth_data['volume'].mean() if len(eth_data) > 0 else 0,
    }

    registry.close()
    calendar.close()

    return df_with_sessions, stats
```

---

## Error Handling

### Best Practices

```python
from manta_trading.data.base.instrument_registry import InstrumentRegistry

# Always use try/finally for cleanup
registry = InstrumentRegistry(db_config)

try:
    # Do work
    instrument = registry.get_instrument_by_canonical_id('AAPL.NASDAQ')

    # Handle not found
    if instrument is None:
        print("Instrument not found")
        return

    # Use instrument
    print(f"Found: {instrument.symbol}")

except ValueError as e:
    # Handle validation errors
    print(f"Validation error: {e}")

except Exception as e:
    # Handle other errors
    print(f"Unexpected error: {e}")

finally:
    # Always close connection
    registry.close()
```

### Context Manager Pattern

```python
from contextlib import contextmanager

@contextmanager
def get_registry(db_config):
    """Context manager for InstrumentRegistry."""
    registry = InstrumentRegistry(db_config)
    try:
        yield registry
    finally:
        registry.close()

# Usage
with get_registry(db_config) as registry:
    instrument = registry.get_instrument_by_canonical_id('AAPL.NASDAQ')
    print(f"Found: {instrument.symbol}")
# Automatically closed
```

---

## Performance Tips

1. **Use Caching**: Registry and Calendar use LRU caching - reuse instances when possible
2. **Batch Queries**: Use `list_instruments()` instead of multiple `get_instrument()` calls
3. **Session Classification**: For large DataFrames, `add_session_column()` is faster than row-by-row
4. **Connection Pooling**: In production, use connection pooling for better performance
5. **Timezone Awareness**: Always use timezone-aware timestamps to avoid conversion overhead

---

## See Also

- [Database Schema Documentation](../database/foundation_schema.md)
- [Migration Guide](../database/migration_750_guide.md)
- [Module Reference](../api/module_reference.md)

---

**Last Updated**: 2025-01-22
**Schema Version**: 1.0
**Migration**: 750
