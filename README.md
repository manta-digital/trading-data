# manta-trading

Market-data acquisition, storage, and serving for US equities and Kalshi event
contracts. CLI-first, TimescaleDB storage. PyPI distribution
`manta-trading-data`, import package `manta_trading`, CLI entry point `mt`.

---

## Overview

The system runs two independent acquisition pipelines into one TimescaleDB
instance, and serves the equities side over a read-only HTTP API:

- **Equities** — daily and minute OHLCV from EODHD across an ~33k-symbol US
  registry (Finnhub enriches IPO dates), with corporate actions,
  adjusted-on-read pricing, per-symbol gap tracking and repair, named symbol
  lists, and point-in-time index universes (survivorship-bias-free S&P 500
  membership). See [Typical workflows](#typical-workflows).
- **Kalshi event contracts** — the full market catalog (series, events,
  markets, settlements), 1-minute candlesticks for a configurable slice of the
  market universe, the exchange-wide public trade tape, and a historical
  backfill that walks the archive back to a configured floor. See
  [Kalshi event-contract data](#kalshi-event-contract-data).
- **Storage** — TimescaleDB hypertables plus continuous aggregates for coarser
  equity grains (5m, 15m, 1h, 4h, …). Schema is owned by a migration chain in
  three tracks (`minute`, `daily`, `kalshi`); the chain is the single source of
  schema truth. See [Setting up a new database](#setting-up-a-new-database).
- **Serving** — `mt serve` runs a read-only FastAPI server for equity bars,
  instrument metadata, gaps, and data health. See
  [Data Serving API](#data-serving-api).
- **Operations** — production runs as bounded passes under systemd timers (not
  a long-lived daemon), with an hourly `mt data health` check, an operator
  wrapper `mt-run`, and offsite backups to S3-compatible storage. See
  [Production deployment](#production-deployment).

Top-level CLI map:

| Command | Purpose |
|---|---|
| `mt data …` | Everything acquisition/storage side: init, migrate, daemon, pull, get, status, health, ca, lists, universes, caggs, restore, rechunk, extend, kalshi |
| `mt serve` | Read-only data serving API |
| `mt status` | System overview: config summary (redacted DB URL) + connectivity check |
| `mt config` | Inspect resolved configuration: `list`, `get`, `set`, `path` |
| `mt provider` | Data-provider registry: `list`, `status`, `test` (credential check) |
| `mt update` | Self-update an installed (non-dev) copy from PyPI |

---

## Installation

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install manta-trading-data
```

`mt --help` should work immediately — no clone, no virtualenv activation.

The package is published on PyPI as **`manta-trading-data`**, but the Python
import package is still `manta_trading` (`import manta_trading`) and the CLI
command is still `mt` — only the install/upgrade name changed.

### Updating

```sh
mt update          # check PyPI and install a newer release (prompts first)
mt update --yes    # non-interactive: install without prompting
mt update --json   # pure query: report versions, change nothing
```

`mt update` upgrades `uv tool` installs itself; on pipx or pip installs it
prints the right command for your environment instead of running it. The
equivalent manual command is always:

```sh
uv tool install --upgrade --refresh-package manta-trading-data manta-trading-data@latest
```

(`--refresh-package` matters right after a release: uv resolves against
cached index metadata, so without it the upgrade can succeed while installing
nothing. `mt update` runs this exact command and verifies the version moved.)

In a development (editable/source) checkout `mt update` refuses and points you
at `git pull && uv sync` — it makes no network call there.

### Development setup

To work on the code itself, use a source checkout instead:

```sh
git clone https://github.com/manta-digital/trading-data
cd trading-data
uv sync
source .venv/bin/activate   # macOS / Linux
# .venv\Scripts\activate    # Windows
```

`mt --version` reports `dev` in a source checkout (no installed distribution
metadata to read); a `uv tool install` reports the real published version.

---

## Environment

Copy `.env_sample` to `.env` and fill in the values. All variables use the
`MT_` prefix. `.env_sample` carries fuller commentary on each; this table is
the summary.

### Core

| Variable | Required | Description |
|---|---|---|
| `MT_TIMESCALE_DB_URL` | Yes | Application credential — **DML only**. Used by the daemon, API server, and every CLI read path. A leak of this URL cannot TRUNCATE, DROP, or write the migration ledger. |
| `MT_TIMESCALE_MAINTENANCE_URL` | For schema/maintenance commands | Migration/maintenance credential — DDL rights. Needed only by `mt data init`, `mt data migrate apply`, `mt data rechunk`, `mt data caggs repair`, `mt data caggs refresh`, and `mt data restore run`. Those commands fail loudly naming this variable when it is unset — they never fall back to the application URL. Leave unset for normal operation. Provision both roles with `scripts/provision_roles.sql` (run as a superuser; idempotent). |
| `MT_EODHD_API_KEY` | Yes | EODHD API token (equity acquisition + universe rebuild) |
| `MT_FINNHUB_API_KEY` | Recommended | Finnhub token (IPO-date enrichment for instruments) |
| `MT_LOG_LEVEL` | No | `DEBUG`, `INFO` (default), `WARNING`, `ERROR` |

### Acquisition tuning

| Variable | Default | Description |
|---|---|---|
| `MT_MINUTE_PROVIDER` | `eodhd` | Minute data provider |
| `MT_DAILY_PROVIDER` | `eodhd` | Daily data provider |
| `MT_EODHD_DAILY_LIMIT` | `100000` | Daily API credit cap |

### Kalshi

See [Kalshi event-contract data](#kalshi-event-contract-data) for what these
control. All are optional — with none set, the client runs unauthenticated at
the public rate tier and the default collection rule applies.

| Variable | Default | Description |
|---|---|---|
| `MT_KALSHI_API_KEY_ID` | — | API key id for authenticated mode. Both auth variables or neither; the client refuses a partial pair at construction. |
| `MT_KALSHI_PRIVATE_KEY_PATH` | — | Path to the RSA private-key PEM file — the **path**, never the key itself. Under systemd the PEM must live outside `/home` (the units set `ProtectHome=true`); documented placement is `/etc/manta-trading-kalshi.pem`, `0640 root:manta-trading`. |
| `MT_KALSHI_REQUESTS_PER_MINUTE` | per-mode default | Rate-budget override (> 0); replaces the built-in public/authenticated budget. |
| `MT_KALSHI_COLLECTION_TRADED_ONLY` | `true` | Candle collection: only markets traded in the last 24h (lifetime volume once settled). |
| `MT_KALSHI_COLLECTION_CATEGORIES` | empty | Allow-list, comma-separated; empty = every category. |
| `MT_KALSHI_COLLECTION_EXCLUDED_CATEGORIES` | `Sports,Mentions` | Exclude-list; exclude wins over allow. |
| `MT_KALSHI_COLLECTION_EXCLUDED_SERIES_PATTERN` | `MENTION\|SAY` | PostgreSQL regex over `series.ticker`, case-sensitive. |
| `MT_KALSHI_COLLECTION_EXCLUDED_TITLE_PATTERN` | `\m(say\|says\|mention\|mentions)\M` | Regex over `series.title`, case-insensitive. |
| `MT_KALSHI_TRADES_EXCLUDED_CATEGORIES` | empty | Trades-tape filter: trades of these categories are counted but not stored (empty = no filtering). Candles for the same categories keep collecting under the collection rule. |

> **Renamed variables:** the collection-rule variables were previously named
> `MT_KALSHI_CANDLE_*`. Every `mt` command now fails at startup while any old
> name is still set (env or `.env`), naming the replacement — there is no
> silent aliasing.

### Serving API

| Variable | Default | Description |
|---|---|---|
| `MT_API_MAX_BARS_PER_REQUEST` | `75000` | Bars-per-request ceiling used by the range cap |
| `MT_API_STATEMENT_TIMEOUT` | `20s` | Per-connection `statement_timeout` on the API's pools |

### Backup / offsite

Backblaze B2 via its S3-compatible API (so rclone/aws tooling works
unchanged). Use a bucket-scoped application key, never the account master key.
Used by the backup scripts under `scripts/`, not by `mt` itself.

`MT_BACKUP_S3_ENDPOINT`, `MT_BACKUP_S3_KEY_ID`, `MT_BACKUP_S3_APPLICATION_KEY`,
`MT_BACKUP_S3_BUCKET`.

### Test / CI

| Variable | Description |
|---|---|
| `MT_TIMESCALE_TEST_URL` | Admin URL for the integration/load tiers, pointing at the `postgres` maintenance database. Must use `trading_test_admin` (LOGIN CREATEDB and nothing else), never a superuser — `test/integration/data/test_test_admin_role.py` fails the suite if it is repointed at one. |
| `MT_RUN_LOAD_TESTS=1` | Gates `test/load/` |

---

## Setting up a new database

The migration chain is the single source of schema truth. Bringing a fresh,
empty Postgres database to the current schema is one command:

```sh
# 1. Create the database (TimescaleDB extension must be available on the instance).
PGPASSWORD=… createdb -h <host> -U postgres trading

# 2. Provision the DML/DDL role split (idempotent; run as superuser).
psql -h <host> -U postgres -d trading -f scripts/provision_roles.sql

# 3. Point at it and initialize (init needs the maintenance credential).
export MT_TIMESCALE_DB_URL=postgresql://trading_app:…@<host>:5432/trading
export MT_TIMESCALE_MAINTENANCE_URL=postgresql://trading_migrate:…@<host>:5432/trading
mt data init
```

`mt data init` is idempotent — re-running it on a healthy database applies zero
migrations. Use `--validate-only` to inspect without changing anything (works
with the application credential alone).

Migrations are organized in three tracks — `minute` (equity minute + shared
infrastructure, the default), `daily`, and `kalshi`:

```sh
mt data migrate status --track kalshi   # check one track
mt data migrate apply  --track kalshi   # apply pending migrations on it
```

Verify after init:

```sh
mt data migrate status   # all rows should report "applied"
mt data caggs status     # every cagg present, each with a refresh policy
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

# Populate delisted_date for delisted symbols.
mt data instruments populate-delisted-dates
```

### Ongoing data acquisition

Production runs bounded passes on systemd timers (see
[Production deployment](#production-deployment)); the same command serves
ad-hoc and catch-up use interactively:

```sh
# Run daemon indefinitely: daily + minute cycles + once-per-day CA update.
# Defaults to full active universe. Ctrl-C or SIGTERM exits cleanly.
mt data daemon run

# Bounded pass: minute data only, exit when the universe is caught up.
# (This is exactly what the mt-minute-pass systemd unit runs.)
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
# One read-only pass/fail check across the whole system: raw minute/daily
# freshness, every cagg's materialization lag, EODHD quota headroom, and
# Kalshi phase recency. One line per check; exit 0 pass, 1 breach,
# 2 could-not-run. This is what the hourly mt-health systemd unit runs.
mt data health
mt data health --json

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
# Status of all caggs (last refresh, policy, row counts).
mt data caggs status

# Manually refresh all caggs (useful after a large backfill; needs the
# maintenance credential).
mt data caggs refresh

# Refresh a specific granularity or window.
mt data caggs refresh --granularity 1h

# Compare cagg contents against source data; repair divergence.
mt data caggs verify
mt data caggs repair

# Rebuild the coverage aggregates that back `available` ranges.
mt data caggs rebuild-coverage
```

### Backup and restore

Nightly/weekly backup and offsite-sync scripts live under `scripts/`
(`backup_prod.sh`, `offsite_sync.sh`, `check_archive_health.sh`, …), targeting
S3-compatible storage via the `MT_BACKUP_S3_*` variables. Restore is a CLI
concern:

```sh
mt data restore assess   # read-only: what would a restore involve?
mt data restore run      # perform it (needs the maintenance credential)
```

The full procedure is documented in the backup-and-restore runbook (see
[Production deployment](#production-deployment)).

### Trading session horizon

```sh
# Extend trading_sessions for all calendars (usually automatic via daemon/status).
mt data extend

# Extend a specific calendar.
mt data extend --calendar NYSE

# Alert if horizon is < 90 days out (useful in CI).
mt data extend --strict
```

---

## Kalshi event-contract data

An independent acquisition pipeline for [Kalshi](https://kalshi.com) event
contracts, stored in its own `kalshi` schema alongside the equity data:

- **Catalog** — series, events, and markets (`kalshi.series` / `kalshi.events`
  / `kalshi.markets`), including settlement results. Write-on-change upserts; a
  persisted watermark drains the settled stream in 6-hour windows, and an
  awaiting-settlement set guarantees markets that closed but have not yet
  settled are re-checked.
- **Candlesticks** — 1-minute candles (`kalshi.candlesticks`, a hypertable)
  for markets selected by the **collection rule** (the
  `MT_KALSHI_COLLECTION_*` variables: traded-only, category allow/exclude
  lists, series/title exclusion regexes). Per-market watermarks.
- **Trades** — the exchange-wide public trade tape (`kalshi.trades`, a
  hypertable with 7-day chunks, compressed after 14 days), walked
  oldest-first in one-hour windows under a single watermark. Trades for
  unknown markets (the multi-leg tape) are counted and dropped, never an
  error.
- **Historical backfill** — an archive walk plus behind-cutoff candles and a
  backward trade-tape drain, filling history from before the pipeline was
  installed back to a configured floor.

### Commands

```sh
# One bounded collection pass: every phase in order
# (catalog → candles → trades → historical). This is what the hourly
# mt-kalshi-pass systemd unit runs. Deliberately takes no phase selection.
mt data kalshi pass

# Full walk of the live catalog, the settled stream, and the awaiting set —
# the replay/repair tool. --settled-since must carry a UTC offset.
mt data kalshi sync
mt data kalshi sync --settled-since 2026-08-01T00:00:00+00:00

# Catalog counts, settlement watermark, awaiting-settlement set, and the
# candle / trades / historical blocks. Reads the database only — no API
# call, and reports sensibly before any sync has ever run.
mt data kalshi status
```

All three take `--json`. Shared exit codes: `0` OK, `1` preflight failure,
`2` provider abort, `3` partial (item-level errors), `4` storage abort. A
Kalshi command run before the `kalshi` migration track is applied exits `1`
naming the missing migration.

### Pass semantics

A pass is bounded: it runs each phase once, in order, and exits. An **abort**
(provider or storage) stops the pass and reports the remaining phases as
skipped; a **partial** (individual item errors) does not stop it. The pass
outcome is the worst phase outcome.

The historical phase runs last and self-limits to thirty minutes of the
client's rate budget per firing. Within a firing it: finishes the archive walk
if incomplete (resumable cursor; the first firing after install runs hours,
not minutes), fetches behind-cutoff candles for up to 1,000 markets, then
drains the trade tape backward toward the historical floor (2026-01-01 UTC).
Once the archive walk is done and the tape reaches the floor, the phase's
steady-state work is just the candle top-up, and firings shorten accordingly.
`mt data kalshi status` shows the descent progress and the effective coverage
floor.

### Authentication and rate budget

The client runs in one of two modes: **public** (unauthenticated, conservative
rate tier) or **authenticated** (`MT_KALSHI_API_KEY_ID` +
`MT_KALSHI_PRIVATE_KEY_PATH`, higher tier). Set both auth variables or
neither. `MT_KALSHI_REQUESTS_PER_MINUTE` overrides either budget.

---

## Data Serving API

```sh
# Start the API server (default: 0.0.0.0:8100).
mt serve

# Custom host/port, multiple workers.
mt serve --host 127.0.0.1 --port 8200 --workers 4

# Dev mode with auto-reload.
mt serve --reload
```

The API serves equity data only; Kalshi data is read via `mt data kalshi
status` or SQL for now.

API endpoints:
- `GET /api/v1/health` — liveness check, plus a coarse `coverage` freshness signal
- `GET /api/v1/bars/{symbol}?granularity=1d&start=…&end=…&adjusted=true` — OHLCV bars.
  Responses carry `is_stale`: `true` means the continuous aggregate serving this
  granularity is behind its source, so the bars may be incomplete. Raw grains
  (`1m`, `1d`) are never stale by construction.
- `GET /api/v1/symbols?search=<prefix>` — list instruments
- `GET /api/v1/symbols/{symbol}` — instrument detail + available data ranges.
  See [`available` semantics](#available-semantics) below for what the reported
  range does and does not guarantee.
- `GET /api/v1/status?symbol=…&health=…&granularity=…&all=true` — per-symbol
  data-health rows, a whole-registry health summary, and coverage freshness.
  **`rows` defaults to unhealthy entries only** (`GAPS`, `STALE`, `FAILED`),
  matching `mt data status`; pass `all=true` for everything or `health=OK` for
  healthy rows. A healthy symbol therefore returns `count: 0` by default — that
  means "nothing wrong", not "no such symbol". `summary` is always the full
  unfiltered whole-registry breakdown, whatever `rows` was filtered to.
- `GET /api/v1/gaps/{symbol}?granularity=1m` — data gap listing
- `GET /docs` — Swagger UI

The full schema is committed at [`docs/api/openapi.json`](docs/api/openapi.json)
and regenerated with `uv run python scripts/dump_openapi.py` (no database
required); a test fails the build on drift.

### `available` semantics

`GET /api/v1/symbols/{symbol}` reports one `{start, end}` per granularity. The
two ends are computed differently and carry different guarantees, which matters
if you use them to decide what to request:

- **`end` is exact.** It comes from a direct probe of the bar tables, bounded so
  it stays fast, and it reflects data written right up to the moment of the
  request. If a bar exists, `end` includes it.
- **`start` is as of the last coverage materialization.** It comes from the
  coverage continuous aggregates, which a background policy refreshes. Deep
  history *backfilled after* the relevant coverage bucket was last materialized
  will not move `start` until that bucket is rebuilt — so `start` can be later
  than the true first bar, never earlier. There is no cheap exact answer here:
  probing below the coverage floor costs 0.4–1.4 s per symbol on production
  (measured), because the bound excludes chunks *after* the start, which for a
  symbol with deep history is almost none of them.

Both ends are UTC dates. A granularity with no data is omitted entirely — an
empty `available` means "no bars for this symbol", not "unknown symbol" (an
unknown symbol is a `404`).

**One documented gap.** The leading-edge probe is bounded by a universe-wide
coverage edge rather than each symbol's own. A bar could in principle be missed
if it falls between an individual symbol's coverage end and that universe edge
*and* was written after coverage last materialized. Measured across a 28-symbol
sample on production 2026-08-04 — dense, delisted, daily-only, and no-data
instruments — the merged answer was **identical to a direct `MIN/MAX` scan for
every symbol**, and no symbol had a single raw bar inside that window. The gap
closes on its own when the coverage refresh repair lands.

### Error shapes

Every error this server raises has the same body:

```json
{ "error": "<message>" }
```

The one deliberate exception is FastAPI's own request-validation failure — an
unparseable date, an unknown `granularity` — which keeps its native body so
clients retain the per-field detail:

```json
{ "detail": [ { "loc": ["query", "granularity"], "msg": "…", "type": "…" } ] }
```

| Status | Meaning |
|---|---|
| `404` | The symbol is not in `instruments`. **Only** that. |
| `422` | The request is malformed, the range is reversed, or the window exceeds the bar ceiling. |
| `500` | An unexpected server fault. The body is sanitized. |
| `504` | The database cancelled the query at the statement timeout. Narrow the range or use a coarser granularity. |

### Date windows are inclusive at both ends

`start` and `end` are both inclusive, at every granularity: `start=2024-06-10&end=2024-06-14`
returns Monday through Friday, and `start=2024-06-10&end=2024-06-10` returns that
whole day. Timestamps are UTC, and the store covers 08:00–23:59 UTC.

### Empty windows are `200`, not `404`

A known symbol with no bars in the requested window returns `200` with
`count: 0` and `bars: []` — a weekend, a holiday, or a pre-listing date is not
an error. `is_stale` is still populated, so "no bars *and* the aggregate is
stale" is distinguishable from "no bars because the market was closed". A `404`
means exactly one thing: the symbol is unknown.

### Range cap

A bars request is admitted or rejected **before any database work**, from an
estimate computed from the window alone: `span_days × bars_per_trading_day ×
(252/365)`. Exceeding `MT_API_MAX_BARS_PER_REQUEST` (default 75,000) is a `422`
whose message names the estimate, the ceiling, and the maximum span for that
granularity. There is no pagination and no silent truncation.

Because the store covers extended hours (08:00–23:59 UTC, ~960 one-minute bars
on a dense day), the cap binds only at intraday grains:

| Granularity | Max span per request (at 75,000) |
|---|---|
| `1m` | ~113 days |
| `5m` | ~565 days |
| `15m` | ~1,697 days |
| `1h` and coarser | effectively unbounded |

For bulk history beyond these spans, query TimescaleDB directly rather than
paging over HTTP.

### Server settings

| Variable | Default | Effect |
|---|---|---|
| `MT_API_MAX_BARS_PER_REQUEST` | `75000` | Bars-per-request ceiling used by the range cap. |
| `MT_API_STATEMENT_TIMEOUT` | `20s` | Per-connection `statement_timeout` on all three pools the API opens. A query that exceeds it becomes a `504`. |

Both are read once at startup; changing either requires a server restart. Note
they interact — raising the bar ceiling without also raising the timeout trades
a fast `422` for a slow `504`.

The API is unauthenticated and CORS-open by design: it is read-only and bound to
a LAN host. Exposing it beyond the LAN, or adding any route that writes, makes
authentication a prerequisite.

---

## Production deployment

Production does not run a long-lived daemon. It runs **bounded passes under
systemd timers** from a pinned checkout at `/opt/manta-trading` owned by a
`nologin` service account:

| Unit | Runs | Cadence |
|---|---|---|
| `mt-daily-pass` | `mt data daemon run --daily --stop-when-done` | timer |
| `mt-minute-pass` | `mt data daemon run --minute --stop-when-done` | timer |
| `mt-kalshi-pass` | `mt data kalshi pass` | hourly at :20 UTC |
| `mt-health` | `mt data health` | hourly |
| `mt-serve` | `mt serve` | long-running service |

Unit files live in [`deploy/systemd/`](deploy/systemd/), alongside a resource
slice (`manta-acquisition.slice`) and a journald namespace config.

- **Install/update**: `deploy/install-production.sh --ref <tag>` — idempotent,
  deploys a readable tag (e.g. `prod-20260823`). It enables nothing by itself;
  turning a timer on is an explicit operator action
  (`sudo systemctl enable --now mt-kalshi-pass.timer`).
- **Operator front door**: [`deploy/mt-run`](deploy/mt-run) — `mt-run
  daily|minute|kalshi` fires a pass now, `mt-run status` shows every unit,
  `mt-run follow [unit]` tails logs, and `mt-run <any mt command>` runs it as
  the service account.
- **Environment**: the production env file lives at `/etc/manta-trading.env`
  ([`deploy/manta-trading.env.example`](deploy/manta-trading.env.example)
  documents it, including the Kalshi variables and PEM placement).
- **Runbooks**: operational procedures (production operations, backup and
  restore, cagg maintenance, test cluster) are indexed at
  [`project-documents/user/runbooks/__readme.md`](project-documents/user/runbooks/__readme.md).

---

## Integration tests

Most integration tests under `test/integration/` require `MT_TIMESCALE_DB_URL`
set to a database that already has the schema applied. They run against that DB
and use a per-test fixture to reset state.

Tests that create throwaway databases (e.g. `test/integration/test_cold_start.py`)
additionally require `MT_TIMESCALE_TEST_URL` — an admin connection using the
`trading_test_admin` role (LOGIN CREATEDB only, never a superuser; see
[Test / CI](#test--ci) above):

```sh
export MT_TIMESCALE_TEST_URL=postgresql://trading_test_admin:…@<host>:5432/postgres
uv run --extra dev pytest test/integration/test_cold_start.py
```

Load tests under `test/load/` are gated behind `MT_RUN_LOAD_TESTS=1`.

---

## Project structure

```
src/manta_trading/
  api/                   # Outbound provider HTTP clients (EODHD, Finnhub)
  api_server/            # FastAPI app (mt serve)
  cli/                   # Typer CLI (mt); commands/ per top-level group
  config/                # Settings (pydantic-settings, MT_* env vars)
  data/
    acquisition/         # Daemon, orchestrators, gap tracking
    adjustment/          # Adjusted-on-read: compute_k_factor, adjusted()
    base/                # InstrumentRegistry, TradingCalendar
    kalshi/              # Kalshi client, collection pass, sync + repositories
    maintenance/         # auto_extend, status_queries
    universe/            # EODHD symbol-list client, Finnhub IPO client
  market/
    schema/              # Migration tracks (minute / daily / kalshi) + runner
  providers/             # Provider registry, auth strategies, error taxonomy
config/
  symbol-lists.yaml      # Named symbol lists (priority1, priority2 / sp500)
deploy/
  systemd/               # Production units + timers
  install-production.sh  # Idempotent install/update by tag
  mt-run                 # Operator wrapper
scripts/                 # Backup/offsite, role provisioning, OpenAPI dump,
                         # operator cutover scripts (see scripts/README.md)
test/
  unit/                  # Unit tests (no DB required)
  integration/           # Integration tests (require MT_TIMESCALE_DB_URL)
  load/                  # Load tests (gated by MT_RUN_LOAD_TESTS=1)
```
