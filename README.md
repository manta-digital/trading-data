# manta-trading

Data acquisition, storage, and serving for equities. CLI-first, EODHD-backed,
TimescaleDB storage. Python package `manta-trading`, CLI entry point `mt`.

---

## Installation

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/manta-digital/trading-data
cd trading-data
uv sync
source .venv/bin/activate   # macOS / Linux
# .venv\Scripts\activate    # Windows
```

`mt --help` should work after activation.

---

## Environment

Copy `.env_sample` to `.env` and fill in the values.

| Variable | Required | Description |
|---|---|---|
| `MT_TIMESCALE_DB_URL` | Yes | PostgreSQL connection URL for the TimescaleDB instance |
| `MT_EODHD_API_KEY` | Yes | EODHD API token (data acquisition + universe rebuild) |
| `MT_FINNHUB_API_KEY` | Recommended | Finnhub token (IPO-date enrichment for instruments) |
| `MT_LOG_LEVEL` | No | Log level: `DEBUG`, `INFO` (default), `WARNING`, `ERROR` |
| `MT_MINUTE_PROVIDER` | No | Minute data provider (default: `eodhd`) |
| `MT_DAILY_PROVIDER` | No | Daily data provider (default: `eodhd`) |
| `MT_EODHD_DAILY_LIMIT` | No | Daily API credit cap (default: `100000`) |

---

## Setting up a new database

The migration chain is the single source of schema truth. Bringing a fresh,
empty Postgres database to the current schema is one command:

```sh
# 1. Create the database (TimescaleDB extension must be available on the instance).
PGPASSWORD=… createdb -h <host> -U postgres trading

# 2. Point at it and initialize.
export MT_TIMESCALE_DB_URL=postgresql://postgres:…@<host>:5432/trading
mt data init
```

`mt data init` is idempotent — re-running it on a healthy database applies zero
migrations. Use `--validate-only` to inspect without changing anything.

Verify after init:

```sh
mt data migrate status   # all rows should report "applied"
mt data caggs status     # 7 caggs, all with a refresh policy
```

---

## Typical workflows

### First-time universe build

```sh
# Rebuild the instrument registry from EODHD (~33k symbols after OTC filter).
# Finnhub enrichment populates first_listing_date and promotes venue from
# transient 'US' to authoritative exchange. Takes ~9 hours at 60 req/min.
mt data instruments rebuild

# Skip Finnhub if you want registry populated quickly without IPO dates.
mt data instruments rebuild --skip-finnhub

# Populate delisted_date for delisted symbols (slice 159).
mt data instruments populate-delisted-dates
```

### Ongoing data acquisition

```sh
# Run daemon indefinitely: daily + minute cycles + once-per-day CA update.
# Defaults to full active universe. Ctrl-C or SIGTERM exits cleanly.
mt data daemon run

# Limit to minute data only; exit when universe is fully caught up.
mt data daemon run --minute --stop-when-done

# Limit to a named list; stop when done.
mt data daemon run --list priority1 --stop-when-done

# Limit to specific symbols; stop when done (--stop-when-done implied).
mt data daemon run --symbols AAPL,MSFT,SPY

# Cap credit spend.
mt data daemon run --max-credits 5000
```

### Targeted gap fill

```sh
# Fetch all UNKNOWN daily gaps for the full universe.
mt data pull 1d --universe

# Fetch minute gaps for a specific symbol.
mt data pull 1m --symbol AAPL

# Fetch minute gaps for a named list, verbose progress.
mt data pull 1m --list priority1 -v

# Preview what would be fetched without making changes.
mt data pull 1m --universe --dry-run

# Reset terminal gaps (PROVIDER_HOLE / RETRY_EXHAUSTED) then refetch.
mt data pull 1m --symbol AAPL --reset

# Include delisted symbols (requires --universe).
mt data pull 1d --universe --include-delisted
```

### Reading data

```sh
# Read adjusted daily bars for AAPL (default: adjusted=True).
mt data get AAPL 1d

# Read raw minute bars for a date range.
mt data get AAPL 1m --start 2024-01-01 --end 2024-03-31 --raw

# Output as JSON or CSV.
mt data get AAPL 1d --json
mt data get AAPL 1d --csv
```

### System health

```sh
# Show non-OK symbols (GAPS, STALE, FAILED) — default view.
mt data status

# Show all symbols including OK.
mt data status --all

# Drill into one symbol: detail panel + full gap listing.
mt data status --symbol AAPL

# Filter to daily or minute only.
mt data status --daily
mt data status --minute

# Machine-readable output.
mt data status --json
```

### Corporate actions

```sh
# Bulk-fetch yesterday's splits + dividends for the full exchange (200 credits).
mt data ca update

# Full history for a single symbol.
mt data ca update --symbol AAPL

# Full history for a named list.
mt data ca update --list priority1

# Inspect stored CA data.
mt data ca show --symbol AAPL
mt data ca list --from 2024-01-01 --to 2024-12-31
```

### Symbol lists and index universes

```sh
# List defined named lists with member counts.
mt data lists ls

# Print members of a list.
mt data lists show priority1

# Refresh the S&P 500 snapshot.
mt data lists refresh-sp500

# Show tracked index universes.
mt data universes ls

# Members of SP500 as of a date (point-in-time, survivorship-bias-free).
mt data universes as-of --name sp500 --date 2020-01-01

# Refresh index constituent tracking from source.
mt data universes refresh
```

### Continuous aggregates

```sh
# Status of all 7 caggs (last refresh, policy, row counts).
mt data caggs status

# Manually refresh all caggs (useful after a large backfill).
mt data caggs refresh

# Refresh a specific granularity.
mt data caggs refresh --granularity 1h
```

### Trading session horizon

```sh
# Extend trading_sessions for all calendars (usually automatic via daemon/status).
mt data extend

# Extend a specific calendar.
mt data extend --calendar NYSE

# Alert if horizon is < 90 days out (useful in CI).
mt data extend --strict
```

### Schema migrations

```sh
# Check migration state.
mt data migrate status

# Apply pending migrations.
mt data migrate apply
```

### Data Serving API

```sh
# Start the API server (default: 0.0.0.0:8100).
mt serve

# Custom host/port, multiple workers.
mt serve --host 127.0.0.1 --port 8200 --workers 4

# Dev mode with auto-reload.
mt serve --reload
```

API endpoints:
- `GET /api/v1/health` — liveness check
- `GET /api/v1/bars/{symbol}?granularity=1d&start=…&end=…&adjusted=true` — OHLCV bars
- `GET /api/v1/symbols?search=<prefix>` — list instruments
- `GET /api/v1/symbols/{symbol}` — instrument detail + available data ranges
- `GET /api/v1/gaps/{symbol}?granularity=1m` — data gap listing
- `GET /docs` — Swagger UI

---

## Integration tests

Most integration tests under `test/integration/` require `MT_TIMESCALE_DB_URL`
set to a database that already has the schema applied. They run against that DB
and use a per-test fixture to reset state.

`test/integration/test_cold_start.py` is the exception: it creates and drops
throwaway UUID-named databases for each test, so it requires an admin connection:

```sh
export MT_TIMESCALE_TEST_URL=postgresql://postgres:…@<host>:5432/postgres
uv run --extra dev pytest test/integration/test_cold_start.py
```

CI wiring for integration tests is tracked in issue #17.

---

## Project structure

```
src/manta_trading/
  api/                   # Outbound provider HTTP clients (EODHD, Finnhub)
  api_server/            # FastAPI app (mt serve)
  cli/                   # Typer CLI commands
  config/                # Settings (pydantic-settings, MT_* env vars)
  data/
    acquisition/         # Daemon, orchestrators, gap tracking
    adjustment/          # Adjusted-on-read: compute_k_factor, adjusted()
    base/                # InstrumentRegistry, TradingCalendar
    maintenance/         # auto_extend, status_queries
    universe/            # EODHD symbol-list client, Finnhub IPO client
  market/
    schema/              # Migration definitions and runner
config/
  symbol-lists.yaml      # Named symbol lists (priority1, priority2 / sp500)
test/
  unit/                  # Unit tests (no DB required)
  integration/           # Integration tests (require MT_TIMESCALE_DB_URL)
```
