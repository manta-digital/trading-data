---
docType: changelog
scope: project-wide
---

# Changelog
All notable changes to manta-trading will be documented in this file. Entries are written from the user's perspective and answer:
* What can I do now that I couldn't do before?
* What specific bugs, if any, are fixed?
* Were any features removed?

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added (slice 261 — Kalshi provider foundation, complete 2026-08-24)
- **Kalshi is a registered provider.** `mt provider list` shows `kalshi`
  (auth: none — market data is public). New package
  `manta_trading.data.kalshi`: an async `trade-api/v2` client covering series,
  events, markets, candlesticks, trades and the historical cutoff, with
  cursor-following iterators, one shared rate budget, bounded retry, and a
  transient/permanent error taxonomy that is complete over every httpx
  failure mode. Responses are typed Pydantic models; fixed-point money fields
  parse to `Decimal`.
- **Optional authenticated mode.** Set `MT_KALSHI_API_KEY_ID` and
  `MT_KALSHI_PRIVATE_KEY_PATH` (a PEM file path — never key material) and the
  client signs requests (RSA-PSS) and runs on the documented authenticated
  budget; set neither for public mode; setting only one is a construction-time
  error. New direct dependency: `cryptography`.
- **`kalshi` migration track.** `mt data migrate apply|status --track kalshi`
  creates PostgreSQL schema `kalshi` on the trading database: catalog tables
  (`series`, `events`, `markets` with an enum-derived status CHECK and the raw
  payload) and collection-state tables (`sync_state`, `awaiting_settlement`,
  `market_candle_state`). `--track` defaults to `minute`, so existing
  `mt data migrate` behaviour is unchanged. Not yet applied to production.
- **Recorded real-response fixtures** under `test/fixtures/kalshi/` and the
  developer recorder `scripts/record_kalshi_fixtures.py` (`--only`, `--dry-run`).

### Changed
- Discovery finding recorded in the 261 design: Kalshi *serves* market status
  as `initialized`/`active`/`inactive`/`closed`/`determined`/`finalized`; the
  documented `unopened`/`open`/`paused`/`closed`/`settled` vocabulary is the
  query filter only. Slice 266 (historical backfill) is confirmed viable —
  public `/historical/*` data exists behind a discoverable cutoff.

## [0.8.0] — 2026-08-23

### Added (slice 916 — supervised production, complete 2026-08-23)
- **Production now runs itself.** The daily and minute acquisition passes fire
  from systemd timers (00:35/12:35 and 01:05/13:05 UTC), `mt serve` runs as a
  supervised service, and a reboot or crash needs no operator action: services
  restart, timers return, and `Persistent=true` catches up any schedule missed
  while the host was down. Production is a dedicated pinned checkout at
  `/opt/manta-trading` under a `nologin` service account — the dev checkout is
  development-only and no longer what production means.
- **`mt-run` — one front door for operating production.** `mt-run daily|minute`
  runs a pass with live terminal output (Ctrl-C detaches; the pass keeps
  running), `mt-run status` shows what's running with its latest output,
  `mt-run follow` re-attaches, and `mt-run <anything>` runs any production
  `mt` command (e.g. `mt-run data caggs status`) from any directory.
- **One-command install/update:** `deploy/install-production.sh --ref <tag>`,
  idempotent and check-then-act; deploys use readable tags (e.g.
  `prod-20260823`), never raw SHAs. Enables nothing by itself — cutover is an
  explicit `systemctl enable --now`.
- **Runbooks are indexed:** `__readme.md` lists every runbook with a
  when-to-open line; files renamed to `100-production-operations.md`,
  `200-backup-and-restore.md`, `3xx` cagg maintenance, `400` test cluster.

### Fixed (slice 916)
- **EODHD API token no longer leaks into logs.** Retry/error paths in
  `eodhd_sync` logged full request URLs including `api_token` into journald;
  now redacted at every log and exception site.
- `mt serve --help` no longer cites dead slice 155; it names the `mt-serve`
  systemd unit.

### Changed (slice 169, part 1 — schema and thresholds; not yet applied to production)
- **The coverage aggregates behind `mt data status` now bucket at 7 days instead of a year.** A TimescaleDB refresh policy only re-materializes buckets *fully contained* in its window, so the **current (open) bucket is never written while it is open**. With a year-wide bucket that meant coverage was materialized once, at creation, and then not again until the year rolled over: on 2026-08-11 both aggregates sat at **2025-12-26** while raw data ran to 2026-08-07, and each hourly refresh had reported success 205 times running. Narrowing does not make the engine refresh an open bucket — nothing does — but it cuts the worst-case invisible window from **up to a year to 7 days 4 hours**. Migrations 051/052 carry the change; **production still has the old width until the rebuild in part 2 runs**, so `mt data status` continues to report stale coverage until then.
- **The staleness threshold now describes a bound the system can actually meet.** `COVERAGE_CONTENT_STALENESS` was 1 day 4 hours — unreachable at any bucket width compatible with a fast `data_status` read, so it fired permanently, and a signal that always fires teaches operators to ignore it. It is now derived from the bucket width (7 days 4 hours) rather than chosen. A genuine stall still exceeds one bucket width and still fires.
- **`\d+ data_status` no longer understates the lag by months.** The in-database doc comment derived its "CAGG LAG" bound from the refresh *schedule intervals* alone — "2 hours total" — which bounds only how promptly *closed* buckets are rewritten. It now includes the bucket-width term, and says why.
- **`mt data status` full-universe reads are slower at the new width** — 2.6 s versus 0.5 s before, on a production-shaped database. This is the direct cost of the narrower bucket, and about **37 % of it is a summary of all 12,040 symbols that is computed even when you asked about one symbol** (tracked as issue #16, which would take the single-symbol case to roughly 0.01 s). `/api/v1/health` and the acquisition daemon do not read this view at all and are unaffected.

### Fixed (slice 170)
- **Daily-bar queries are no longer slow before they read anything.** `daily_ohlcv` had been split into **3,372 chunks** by a 7-day chunk interval set at creation and never revisited — the same over-chunking pathology fixed for `minute_ohlcv` (slice 166) and the minute aggregates (slice 163). Planning, not execution, was the cost: a bare `SELECT MAX(time)` took **4.92 s**, and asking for the latest bar across the full 31k-symbol universe could not finish *planning* in two minutes, which is what wedged the daemon for 15 hours on 2026-08-05. The table is now rewritten into 70-day chunks — **341 chunks**, a 10x reduction — and the same probes take **0.157 s** and **7.70 s**. Row counts and per-symbol volume sums are byte-identical before and after; nothing was lost.
- **The daily continuous aggregates were roughly half-empty, and are now complete.** `daily_weekly_ohlcv`, `daily_monthly_ohlcv`, `daily_quarterly_ohlcv`, and `daily_coverage` were missing large spans of history — their refresh policies look back at most 270 days, so a scheduled run could never repair anything older, and the gaps sat there indefinitely while every run reported success. A forced full-span rebuild added **+6.6 M**, **+1.5 M**, **+530 k**, and **+148 k** rows respectively. All three rollups now match their source exactly (closed-window parity of 0). Note this does **not** fix coverage staleness: the 365-day coverage bucket is still never refreshed while it is open, so `mt data status` continues to report data ending 2025-12-26 until slice 169 lands.

### Added (slice 170)
- **`mt data rechunk` now takes `--table minute|daily`.** The maintenance command was hardcoded to the minute table; it now targets either hypertable through a small registry that carries each table's chunk interval, dependent aggregates, and interval migration, so a pre-flight failure names the migration that actually fixes it. The default invocation is unchanged and still plans `minute_ohlcv`.

## [0.7.7] - 2026-08-09

### Removed (slice 914)
- **The non-functional AlphaVantage-era news subsystem is gone.** Slice 152 deleted AlphaVantage but left `src/manta_trading/news/` and `src/manta_trading/agents/newsagent.py` behind as a stub whose every entry point raised unconditionally — no code path through it could ever succeed. AlphaVantage's news value was pre-sentiment-analyzed articles, which realtime feeds don't provide, so this is not a deferral of a news capability; a future news feature would use a different provider and shape entirely, and the deleted code is preserved in git history if ever wanted. Removing it also drops the **`pymongo`** and **`motor`** dependencies, which nothing else in the tree used, and eliminates 4 permanently-failing integration tests plus the `pymongo` server-RTT background thread that intermittently tripped the 30-second pytest timeout and took unrelated tests down with it.

### Security (slice 913)
- **A leaked production credential can no longer destroy anything.** On 2026-08-04 a test fixture received the production URL and ran `TRUNCATE ... CASCADE`, emptying six metadata tables. Every control added afterward was *procedural* — the static ratchet guards, the runtime environment scrub, the additive-allowlist test runner — and each stops a class of caller from *obtaining* the URL without limiting what the URL can *do*. Production connected as `postgres`, so the credential that inserts bars could also drop the database. It now connects as **`trading_app`**, which holds DML only: no `TRUNCATE` (a separately grantable privilege in PostgreSQL, and withholding it is precisely what makes the incident statement fail), no DDL, no ownership, and `SELECT`-only on the migration ledger. The same leak now dies on `permission denied`. This closes the one `sql.md` "Production Database Protection" rule — *Split connection roles* — that was not enforced by the server itself.
- **Schema and maintenance commands take a separate credential.** `mt data migrate apply`, `mt data init`, `mt data restore run`, `mt data rechunk`, `mt data caggs repair`, and `mt data caggs refresh` now resolve **`MT_TIMESCALE_MAINTENANCE_URL`** and **fail loudly when it is unset — they never fall back to the application URL.** A fallback would restore exactly the single-credential coupling this removes, and would turn a clear configuration error into a confusing privilege failure partway through a migration. Read-only variants are deliberately unaffected: `migrate status`, `restore assess`, `init --validate-only`, and the `--dry-run` forms keep working with only the application credential, so an operator can always inspect state.
- **The test tier no longer holds a superuser credential.** `MT_TIMESCALE_TEST_URL` was `postgres` on production's host, and the fixtures' own `swap_dbname` helper will build a URL for any database on that cluster — so a fixture could reach production by changing one path component, prevented only by the convention that fixtures generate `mt_test_*` names. It now points at **`trading_test_admin`**, which holds `CREATEDB`, `CREATEROLE`, and `pg_signal_backend` and nothing else. Measured before and after: as `postgres` that credential held **80,108 table grants** on the production database; it now holds **zero** and cannot read it.
- **Operator note:** provisioning is a one-time administrator action. `scripts/provision_roles.sql` is idempotent and re-runnable but must be applied *as a superuser* — the maintenance role deliberately cannot grant roles, including to itself. New tables are covered automatically by default privileges, so the artifact is re-run only when roles or grants change. Anyone relying on an interactive superuser connection (for example a database IDE) is using the credential that the rollback line in `.env` provides.

### Fixed
- **Ctrl-C / SIGTERM now stops the daemon promptly, even mid-backfill or mid-quota-wait.** The shutdown flag was only polled between symbols — a deep minute backfill walks ~69 chunks per symbol (observed: 13 minutes), and a quota wait re-slept after every signal, so a daily-window wait could ignore shutdown for hours (observed 2026-08-07: five signals logged, zero effect, SIGKILL required). The minute chunk loop now checks the flag at every chunk, and per-chunk commits keep the remaining gaps resumable, so nothing already fetched is lost; a flagged quota wait aborts cleanly instead of deducting and sleeping on.

## [0.7.6] - 2026-08-06

### Fixed
- **The daemon no longer wedges for hours while choosing daily-cycle mode.** The cold-symbol probe was a `COUNT(DISTINCT symbol)` with a ~31k-element `ANY` against the raw `daily_ohlcv` hypertable — unplannable across its 3,371 chunks (planning alone exceeds 120 s) — and daemon sessions carried no statement timeout, so it ran until killed the first time `data_gaps` stopped short-circuiting it (a 15.5-hour stuck backend, 2026-08-05). The probe is now an anti-join on `acquisition_state`, bounded regardless of hypertable chunk count, and all four daemon connection pools set session timeouts so no future query can hang a cycle indefinitely. The underlying over-chunking is tracked as slice 170.

## [0.7.5] - 2026-08-05

### Added
- **`--stop-when-done` waits now report progress.** A wait for the daily gate announced itself once and then went silent for up to thirty minutes — indistinguishable from a hung process. The wait now restates itself with the remaining time every five minutes.

### Changed (slice 187)
- **`GET /api/v1/symbols/{symbol}` went from ~2.7–4.0 s to 16–35 ms.** The endpoint the UI calls to decide what it may request was the slowest thing in the API. It computed `available` with two live `MIN/MAX` queries, and the daily one cost 2.5–4.0 s on production — **96% of it planning**, across `daily_ohlcv`'s 3,371 chunks, before reading a single row. The two queries were also dispatched through `asyncio.gather` on one pooled connection, which psycopg serializes, so the endpoint paid their sum rather than their maximum. `available` is now read from the coverage aggregates for the historical floor merged with a *bounded* probe for the leading edge, all three statements bounded so the planner can exclude chunks. **The response shape is unchanged** — same fields, same 404 contract, same omit-empty-granularity rule, and the committed OpenAPI document shows no diff for this route. Verified against a direct `MIN/MAX` scan across 28 production symbols spanning dense, delisted, daily-only, and no-data instruments: identical answers for every one.
- **Coverage freshness now reports the truth, which means production reports coverage as STALE until the refresh repair lands.** `mt data status`, `GET /api/v1/health` (`coverage`), and `GET /api/v1/status` (`coverage.is_stale`) have been reporting healthy coverage while the daily branch understated the leading edge by 52 days. The freshness check aligns the raw edge onto the aggregate's own bucket grid before comparing — correct for narrow buckets, but the coverage aggregates bucket at 365 days, so no lag under a year was observable and the check returned `is_fresh=True, lag=0` over a 52-day staleness. A content-edge check now compares the aggregate's newest *content* timestamp against its source with no bucket alignment, and raises `CONTENT_EDGE_TOO_OLD`. **This is a visible operator-facing change and it is correct**: nothing starts failing (coverage is reported, not refused), and the underlying staleness was already there. It is tracked as **slice 169**; when that repair lands, these surfaces go green on their own. `GET /api/v1/symbols/{symbol}` is deliberately unaffected — it consults no freshness verdict, which is why its answer is right despite the staleness.

### Added (slice 187)
- **The serving API has a load-test tier.** `test/load/test_187_api_nfr.py` asserts four bounds the unit and integration tiers structurally cannot: symbol-detail latency, full-universe `/api/v1/status` latency, 16-way concurrency against the 8-connection pool, and — the one that motivated the tier — **request** latency for a bars request at the admission ceiling. `statement_timeout` bounds a statement, not a request, so a request can be admitted, issue only fast statements, and still take far longer than any configured timeout. Run it with `MT_RUN_LOAD_TESTS=1 uv run pytest test/load/` and `MT_TIMESCALE_TEST_URL` exported; CI gating for the tier is slice 907's.
- **`create_app(db_url=...)`** lets a test point the API at a specific database without setting `MT_TIMESCALE_DB_URL`. Production behavior is unchanged when the argument is omitted.

### Fixed (slice 187)
- **Available-range dates could be off by one day under a non-UTC database session.** The coverage reads cast their timestamps to `date` without pinning the timezone, so a bar at midnight UTC rendered as the previous day whenever the session timezone was behind UTC. Production was unaffected (its pool sets UTC), but the answer depended on that setting staying put rather than on the query being correct.

### Changed — BREAKING (slice 186)
- **A known symbol with no bars in the requested window now returns `200` with `count: 0`, not `404`.** Until now `GET /api/v1/bars/{symbol}` raised one `404` for two unrelated conditions — the symbol does not exist, and the symbol exists but the window is empty — so a weekend was indistinguishable from a typo, and a client had no way to tell "retry with a different range" from "fix the symbol". `404` now means exactly one thing: the symbol is not in `instruments`. The existence check runs only when the frame comes back empty, so a normal response costs nothing for it, and `is_stale` is still populated on the empty `200` — "no bars *and* the aggregate is stale" is precisely the case that must not look like "no bars because the market was closed". **Clients treating any `404` as "no data for this window" must be updated.**
- **Every error the server raises now uses one body shape, `{"error": "<message>"}`.** Three shapes were in circulation: `{"error": …}` from the 404 and 500 handlers, `{"detail": "…"}` from the status route's two 422s, and FastAPI's `{"detail": [...]}` for request validation. The first two are unified. FastAPI's request-validation body is the one deliberate, documented exception — it carries per-field `loc`/`msg` that a flattened string would lose, and it is what FastAPI-aware clients and code generators expect. **Clients reading `detail` from a non-validation error must be updated to read `error`.**

### Added (slice 186)
- **An over-large bars request now fails immediately with an actionable `422` instead of running until something gives.** A bare 20-year `1m` request was previously admitted and serialized unbounded rows through a single executor thread. Requests are now admitted or rejected before any database work, from an estimate computed from the window alone; the rejection names the estimated bar count, the ceiling, and the maximum span for that granularity. A rejected request checks out no connection. Because the store covers extended hours (measured: ~960 one-minute bars on a dense day, not the 390-minute regular session), the cap binds only intraday — roughly 113 days at `1m`, 565 at `5m`, 1,697 at `15m`, and never at `1d` or coarser. A reversed range (`start > end`), which used to return a misleading `404`, is now a `422` as well.
- **A query cancelled by the server's statement timeout now returns `504` with guidance, not a bare `500`.** The client previously waited the full timeout only to be told "internal server error". The message quotes the *configured* budget, so an operator who raises it is not told the default. A cancelled *freshness* probe still returns `200` with a stale verdict — a `504` always means a data query was cancelled, which is what makes "narrow the requested range" correct advice.
- **Two serving-API ceilings are now operator-settable**: `MT_API_MAX_BARS_PER_REQUEST` (default 75,000) and `MT_API_STATEMENT_TIMEOUT` (default `20s`). Both are validated at startup — a bad value fails the server's launch rather than the first request — and both are read once, so changing either requires a restart. They interact: raising the bar ceiling without room in the timeout trades a fast `422` for a slow `504`.
- **The API's OpenAPI schema is committed at `docs/api/openapi.json`** and regenerated with `uv run python scripts/dump_openapi.py` (no database required). A test fails the build if the committed document drifts from the app.

### Fixed (slice 186)
- **The API's OpenAPI document reported version `0.1.0` while the distribution was at 0.7.3.** Both the CLI's `--version` and the schema's `info.version` now resolve from installed package metadata through one shared function, so the two can no longer disagree.
- **`GET /api/v1/gaps/{symbol}` returned "no gaps" whenever only one of `start`/`end` was supplied.** Passing a single bound routed the request into the two-bound query with the other bound left as SQL `NULL`, and a comparison against `NULL` is never true — so `?start=2024-01-01` on a symbol with 31 known gaps returned `count: 0`, with no error. On the endpoint whose entire purpose is reporting data-integrity holes, that reads as a clean bill of health, which is the most dangerous form the wrong answer could take. One-sided windows now mean what they say: an absent bound is unbounded.
- **`GET /api/v1/gaps/{symbol}` excluded any gap beginning on the `end` date.** Same root cause as the bars fix below — `end` was treated as midnight of that day. `end` is now inclusive here too, so the two endpoints agree on what a date window means.
- **A minute-grain bars request was silently dropping the last day of the requested window.** `end` is inclusive at daily granularities, but the minute path converted it to midnight UTC, making it effectively exclusive — so `start=2024-06-10&end=2024-06-14` at `1m` returned Monday through *Thursday*, 2,975 bars ending 06-13 23:59, with nothing in the response indicating the truncation. `start=end` returned zero bars at every minute granularity, which slice 186's `404`→`200` split then dressed up as a plausible "market was closed" answer. `end` is now inclusive at every granularity: the same request returns 3,764 bars ending 06-14 23:59, and a single-day request returns that day. Requests that worked around this by passing `end + 1` will now receive one extra day.
- **A bars request for a symbol with dividends was taking ~92 seconds; it now takes under 5.** Adjusted-on-read loaded a symbol's *entire* corporate-action history regardless of the window requested, then issued one `SELECT … ORDER BY time DESC LIMIT 1` per dividend to find each ex-date's prior close — ~94 queries for AAPL. Because `daily_ohlcv` spans thousands of chunks, each of those queries cost ~1.8 s in query *planning* against ~110 ms of execution, so nearly all of the 92 s was the planner rebuilding the same plan. The snapshot is now bounded to actions that can affect the requested window (only actions after a bar's date change its price), and the prior closes come from one indexed range scan. Measured on prod: AAPL `1m` over 112 days, 95 s → 5.2 s end-to-end; output is bit-identical across six windows spanning the 2020 4-for-1 split and 1998. This also means the request was never bounded by `MT_API_STATEMENT_TIMEOUT` — that limit applies per statement, and no single one of the 94 exceeded it.
- **A minute query cancelled by the statement timeout no longer reports "no data".** `TimescaleMinuteDataDB.get_minute_data` caught every exception and returned an empty DataFrame, so a cancelled query was indistinguishable from an empty window — and with the `404`/`200` split above, it reached clients as `200 {"count": 0}`, i.e. "the market was closed". Cancellation now propagates and becomes a `504`. Every other failure keeps the previous log-and-return-empty behavior the CLI and daemon rely on.
- **Serving queries no longer run with bulk-import session settings.** The API opens three connection pools, and only one of them was reachable from the server's own configuration — the two that actually serve bars are owned by the data-access classes and ran at `statement_timeout = 300s`, `work_mem = 512MB`, values chosen for bulk COPY and universe-wide aggregation. All three now run at `20s`/`64MB`. CLI and daemon paths keep the bulk values unchanged.

## [0.7.4] - 2026-08-03

### Fixed (slice 912)
- **An interrupted daily pass is now retried the same day, and resumes where it stopped.** Previously the daemon decided whether it had daily work from an in-memory timestamp that recorded when a pass *started*, stamped before the pass ran. A pass that died partway — SIGTERM between symbols, an exception, a stopped session — had therefore already marked the UTC day complete, and nothing retried it: every symbol it had not yet reached went unfetched until the next day. Because that timestamp also lived only in memory, restarting the daemon re-ran the entire alphabet unconditionally, which looked like recovery and hid the underlying fault. The daily cycle now derives its remaining work from `acquisition_state`, which both fetch paths already write, so an interrupted pass is retried within the same UTC day and picks up at exactly the symbols it never reached. A restart changes nothing, because there is no longer any in-memory state to lose.
- **`--stop-when-done` no longer reports a drained scope when it merely hit a closed gate.** A scoped daily run started before the post-midnight grace window exited immediately, logging that its scope was drained, having fetched nothing. Such a run now waits for the gate to open, runs its pass, and then exits. The two idle reasons — "no cycle is due yet" and "no work remains" — are reported distinctly, and the message names the next due time. Unscoped runs and `--forever` are unaffected, and `mt data daemon run --minute --list <name>` still performs exactly one pass and exits.

### Added (slice 912)
- **The daily retry cadence is now tunable without a code change**, via `MT_DAILY_CYCLE_RETRY_MINUTES` or `--daily-retry-minutes` on `mt data daemon run` (default 30 minutes, accepted range 1–1440). This matters during a provider outage: every retry re-issues the bulk EOD call, so the interval is a direct trade between how quickly an interrupted pass resumes and how many credits a sustained outage costs.
- **Scope members the daily cycle cannot act on are now counted and reported rather than silently skipped.** Symbols whose trading calendar has no completed session, and symbols absent from `instruments` entirely, are logged once per cycle with a count and examples — never once per symbol, which is how the condition stayed invisible. The two causes are reported separately, so a mistyped `--symbols` argument is no longer attributed to the known missing-calendar issue.

### Added (slice 185)
- **API clients can now tell "market closed" from "the data pipeline stalled".** Until now a stale continuous aggregate looked identical to a quiet market — a `200 OK` with the same or slightly fewer bars, and a passing health check — even while `mt data status` was already showing an OUT OF DATE banner. Three additive surfaces close that gap, all reusing the freshness machinery `mt data status` already relies on:
  - **`GET /api/v1/status`** (new) returns the same `data_status` health rows the CLI shows, plus a `coverage` block carrying one freshness verdict per coverage aggregate. Filters mirror the CLI flags: `?symbol=`, `?health=OK,GAPS,STALE,FAILED`, `?granularity=daily|minute`, and `?all=true`. Defaults match too — non-`OK` rows only. A symbol with no matching rows is a `200` with `rows: []`, not a `404`. Health values are matched leniently for case and spacing, but a `health` parameter that is present and empty (an unset `?health={filter}` template, say) is rejected with a `422` naming the valid values rather than quietly matching nothing.
  - **`GET /api/v1/health`** gains a `coverage` field (`"ok"` / `"stale"`), a cheap pipeline-liveness signal. It is omitted entirely when the database itself is unreachable, so an outage does not get reported as staleness.
  - **`GET /api/v1/bars/{symbol}`** gains `is_stale` on every response, in both JSON and msgpack. For the seven aggregate-served granularities (`5m`/`15m`/`1h`/`4h`/`1w`/`1mo`/`1q`) it is probed against the exact view that served the request; `1m` and `1d` read raw tables, so they are never probed and always report `false`.

  Stale data is still returned, never withheld — the fields report the condition and leave the decision to the client. Measured cost on production: no measurable latency in steady state (verdicts are cached for 60s process-wide), and ~0.3s–1.3s on a cold cache, within the 2.5s budget the design set.

- No existing response field changed shape or meaning, and `mt data status` is byte-for-byte unchanged — this slice only adds callers.

## [0.7.3] - 2026-08-02

### Fixed (slice 909, code review)
- **`mt update` told pipx users to run `pip`.** Install-method detection resolved the interpreter path before inspecting it, and in every virtualenv `bin/python` is a symlink to the base interpreter — so the `pipx/venvs` and `uv/tools` markers in the path were thrown away before they could be matched. A pipx install was therefore classified as a plain pip install and offered `pip install --upgrade manta-trading-data`, which is the wrong command to run inside a pipx-managed environment. Detection now inspects the unresolved paths and recognises pipx's own `pipx_metadata.json` marker, so pipx installs are identified correctly regardless of where `PIPX_HOME` points. Affects `0.7.0`–`0.7.2`. uv tool installs were unaffected in practice.
- **A malformed version from PyPI could produce a traceback.** `mt update` validated that `info.version` was a non-empty string but not that it was a real version number, so a non-PEP-440 value reached the comparison step and raised. It is now validated at the point of fetch and treated like any other unusable registry response: a clean "could not reach PyPI" message and exit 1.

### Changed
- Failure paths in `mt update` now emit debug-level detail identifying *which* failure occurred (registry unreachable vs. malformed response, probe timeout vs. no database), so a support report can distinguish them. User-facing output is unchanged.

## [0.7.2] - 2026-08-02

### Fixed (slice 909)
- **`mt update` no longer skips an upgrade because uv's cache has not noticed the release yet.** `mt update` asks PyPI directly and sees a new version immediately, but the `uv` command it runs resolves against cached index metadata — so if your cache was populated shortly before a release, the upgrade would run, succeed, and change nothing. It now refreshes this package's metadata as part of the upgrade. (Introduced in `0.7.1`, the version check added there reports this situation honestly rather than claiming a false success, so `0.7.1` never lies about it — it just may need a second run.)

## [0.7.1] - 2026-08-02

### Fixed (slice 909)
- **`mt update` could report a successful upgrade that never happened.** After upgrading, it now re-reads the version from the installed binary and, if the version did not actually move, reports that plainly and exits 1 instead of printing "Updated manta-trading-data to X". Observed on the `0.7.0` release: `mt update` correctly announced `0.6.1 → 0.7.0`, the upgrade exited successfully, and `mt --version` still reported `0.6.1`.

## [0.7.0] - 2026-08-02

### Added (slice 909)
- **`mt update` — check for and install a newer release without leaving the tool.** It asks PyPI what the latest published version is, compares it with the version you are running, and — if you installed with `uv tool install` — upgrades in place after asking for confirmation. `mt update --yes` skips the prompt for scripted use; `mt update --json` is a pure query that reports `current`, `latest`, `update_available`, and `install_method` and changes nothing. When stdin is not a terminal and `--yes` was not passed, it reports what an update would do and takes no action.
- **On pipx and pip installs, `mt update` prints the correct upgrade command for your environment rather than running it**, and in a development (editable/source) checkout it refuses with `git pull && uv sync` — without making any network call.
- **After a successful upgrade, `mt update` reports how many database migrations are pending** so a code upgrade never silently leaves the schema behind. This check runs against the newly installed version and is strictly best-effort: a database that is down, unconfigured, or absent prints a generic "run `mt data migrate status`" pointer and cannot fail the update.
- Registry problems are reported as one clean line, never a traceback: an unreachable PyPI, a non-200 response, or a malformed reply produces `Could not reach PyPI — check your network connection.` and exit code 1. No command other than `mt update` ever contacts PyPI, and `mt --help` still works offline.

## [0.6.1] - 2026-08-01

### Fixed (slice 908)
- **A clean `uv tool install manta-trading-data` crashed immediately on import — `v0.6.0` never actually worked for a new install.** `pyproject.toml` declared `requires-python = ">=3.10,<3.13"`, but the codebase uses `enum.StrEnum` throughout, which does not exist before Python 3.11. `uv tool install` resolves against the declared lower bound in a fresh environment, picked Python 3.10, and every invocation of `mt` failed with `ImportError: cannot import name 'StrEnum' from 'enum'`. The floor is now `>=3.12,<3.13`, matching the project's actual target Python version. `v0.6.0` cannot be corrected in place — a published PyPI version is immutable — so it should be treated as broken; install `0.6.1` or later.

## [0.6.0] - 2026-08-01

### Changed (slice 908)
- **This is the first version published to PyPI, and it is published under a new name: `manta-trading-data`, not `manta-trading`.** `manta-trading` was already reserved on PyPI as a placeholder for a future, unrelated product, so this package needed its own name. Install it with `uv tool install manta-trading-data`; upgrade with `uv tool install --upgrade manta-trading-data`. A source checkout (`git clone` + `uv sync`) still works exactly as before and remains the recommended setup for development.
- **`mt --version` now reports the version PyPI has installed, instead of always printing `dev`** — the version lookup previously checked for a distribution named `manta-trading`, which has never been published, so every install (including source checkouts) reported `dev` regardless of the actual code version. It now checks the published name and reports the real installed version; a source checkout with no installed distribution metadata still correctly reports `dev`.
- **Nothing else changes.** The import package is still `manta_trading` (`import manta_trading`, `from manta_trading... import ...`), the `mt` command name is unchanged, and config file locations (`~/.config/manta-trading/config.toml`, `.manta-trading.toml`) are unchanged.

## [0.5.0] - 2026-07-28

### Fixed (slice 165)
- **`mt data pull 1m --symbol X` is now safe for routine backfill/repair — it seeds coverage-aware, exactly like the daemon** — previously it always fell back to the legacy full-window `[history_start, today]` single-span seed, which could silently re-seed decades of already-present data and burn provider credits; this caused two independent production-verification mistakes during slice 162 and an interim "use `daemon run`, not `pull 1m`" operator workaround (`user/reference/minute-fetch-code-paths.md`, now superseded). `pull 1m` builds a per-symbol coverage index (`WHERE symbol = X` against the 4h cagg — near-instant, no universe scan) and seeds only genuinely-missing sessions. Verified against production: a window containing the 2025-01-09 market-closure hole produced a gap row confined to that single session, not the window span, and `force_reset_terminal` (clearing `PROVIDER_HOLE`/`RETRY_EXHAUSTED` rows) still works unchanged as an orthogonal flag.
- **Every minute/daily fetch now logs which entry point drove it** — a `via=refetch|cycle` field on an entry INFO line (`minute fetch: ADP window=[…] via=refetch`) and on all warning/error lines, so log output alone identifies the code path; nothing observable distinguished them before.
- **The daemon's universe coverage scan no longer times out at current data scale** — the 30s statement timeout was calibrated when the scan produced 2.4M symbol-day pairs (slice 162); continuous backfill grew that 9.4× to 22.7M and the measured end-to-end build is now 152s (row transfer dominates — `statement_timeout` counts streaming, not just aggregation). The timeout is now 300s, sized from measurement with plateau headroom; growth converges, so this holds. Long-term restructuring options are tracked in issue #3, and the full audit lives in `user/reference/prod-scale-and-coverage-scan-baseline.md`.

### Changed (slice 167)
- **`mt data status` full-universe reads are now sub-second instead of ~8 seconds** — the `data_status` view scanned raw `minute_ohlcv` and `daily_ohlcv` to compute each symbol's first/last bar and stored-bar count, so the cost grew with total bar volume: 7.8 s for the full universe even after slice 166's rechunk, and 117 s before it. Coverage is now maintained incrementally by two continuous aggregates (`minute_coverage`, `daily_coverage`, migrations `046`–`049`) bucketed at one year per symbol, and the view reads those instead of raw. The full-universe read measured **170 ms** (count) / **364 ms** (all 63,224 rows) against production, and a load test asserts the sub-second bound on a production-shaped 12,000-symbol database (median 0.744 s).
- **Column output, formatting, and row counts are unchanged** — verified by capturing `mt data status` (full and `--symbol`) before and after against the same production database; the only differences were relative ages that had genuinely advanced. `bars_stored` is exact on both branches and daily timestamps stay exact, because `daily_coverage` reads raw `daily_ohlcv` rather than a parent aggregate. Minute first/last timestamps are truncated to the 4-hour parent-aggregate bucket start and so may read up to 4 hours earlier than the true first/last bar; the view's own doc comment states this bound and the refresh lag alongside it.

### Fixed (slice 167)
- **`mt data status` now tells you when its numbers are out of date instead of quietly serving stale ones** — because coverage is precomputed, a stalled or paused refresh policy would leave the table silently reporting old counts as current. Every reader goes through a guarded accessor that checks both coverage aggregates for freshness first, and prints an `OUT OF DATE` banner (and sets `coverage.is_stale` in `--json`) when a refresh is lagging, paused, failing, or has not succeeded within its budget. Rows are still returned — the command stays usable — and it exits 0, so scripts do not break. Proven by inducing each failure mode against a throwaway database and confirming the banner appears and clears.
- **Pausing a coverage aggregate's refresh policy could not be done with `alter_job`** — TimescaleDB rejects `alter_job(..., scheduled => ...)` on a hierarchical continuous aggregate ("multiple refresh policies are not supported"), which is exactly what `minute_coverage` is. `user/runbooks/cagg-maintenance-pausing.md` now documents the working pause/resume path and the catch-up refresh that must follow it.

### Fixed (slice 168)
- **The minute daemon could silently re-pull data it already had, and only maintenance tooling would notice** — slice 163 stopped `mt data caggs repair` from running against a paused coverage-index cagg, but that guard only covered the maintenance path. A refresh policy that crashed, failed on every fire, or was paused out-of-band with `alter_job` never passed through it, so the daemon kept reading a frozen cagg, kept seeing recent sessions as missing, and kept re-seeding them — burning provider quota with nothing to show for it and no error anywhere. The coverage-index build now asserts its source cagg is actually fresh before trusting it, and on failure logs an ERROR naming the cagg, the measured lag, and exactly which checks tripped, then skips coverage-aware seeding for that cycle. It never falls back to a full-history re-seed and never silently repairs the cagg behind your back — remediation stays an explicit operator action (`user/runbooks/cagg-maintenance-pausing.md`). Four independent checks catch the failure: the cagg lagging its raw source, a paused policy, a policy that has not succeeded within its budget, and a policy whose last run failed. A cagg with no refresh policy at all, or a freshness check that cannot complete, is treated as stale rather than trusted.

### Fixed (slice 163)
- **Minute continuous aggregates were ~79% under-materialized and 36× over-chunked** — the four minute caggs (`minute_5min_ohlcv`, `minute_15min_ohlcv`, `minute_hourly_ohlcv`, `minute_4hour_ohlcv`) each carried ~4,239 chunks at ~1.67 days and were missing most of their history, so anything reading them returned *silently incomplete* data. All four have been rebuilt window-by-window against raw and verified digit-for-digit against `minute_ohlcv`. A single-symbol `minute_4hour_ohlcv` read went from ~5.2 s to ~95 ms (~55× faster: 12,721 plan nodes → 238, planning 1,434–3,201 ms → 66–74 ms), and the four caggs are now fully compressed columnstore. Migrations `044`/`045` keep new chunks at 70 days with compression enabled, so a cold-start database never reaches this state.
- **Minute daemon re-pulled data it already had, indefinitely** — the daemon's coverage index reads the 4h cagg, so whenever that cagg's refresh policy was paused (as during maintenance) recent sessions looked missing and gap rows were re-seeded every cycle, burning provider calls with nothing to show for it. Bars were never duplicated (`ON CONFLICT DO NOTHING`), so the only cost was wasted API quota — and nothing alarmed. `mt data caggs repair` now refuses to run while the coverage-index cagg's refresh policy is paused, and `user/runbooks/cagg-maintenance-pausing.md` documents the catch-up refresh that a resumed policy alone does *not* perform.

### Added (slice 163)
- **`mt data caggs verify`** — reports per-year and per-window parity between each minute cagg and raw `minute_ohlcv`, with `--granularity`, `--detail`, and `--json`. Exits non-zero on any shortfall. Read-only. Note that a shortfall confined to the newest, still-filling window is normal trailing refresh lag rather than data loss; the runbook's closed-window parity query tells the two apart.
- **`mt data caggs repair`** — rebuilds under-materialized cagg windows in place, oldest to newest, over 70-day windows. Resumable: progress is derived from parity rather than bookkeeping, so an interrupted run re-derives its position and skips completed windows on the next invocation. Pre-flight refuses (never warns) unless the target cagg's refresh and columnstore policies are paused, migration 044 is applied, the coverage-index cagg is still refreshing, and disk headroom is attested via `--assume-headroom-gb`. Supports `--dry-run`.

### Fixed (previous)
- **Pathological `minute_ohlcv` query latency** (slice 166) — a trivial single-symbol `MIN(time)/MAX(time)` took 10m47s and a universe-wide existence probe 8m8s, because the hypertable had accumulated 25,256 four-hour chunks and query *planning* alone (846 s, 176k locks) dominated. The table has been rewritten in place to 1,203 seven-day chunks (`mt data rechunk`, resumable, verified zero data loss against pre-rewrite baselines): single-symbol MIN/MAX now ~0.7 s, the universe probe 37.7 s, `mt data status` full-universe 7.8 s (was 117 s), and storage dropped 126 GB → 78 GB. Migration `043` keeps future chunks at 7 days (`MINUTE_OHLCV_CHUNK_INTERVAL`), including on cold-start databases.

### Added (slice 166)
- **`mt data rechunk`** — one-shot, resumable maintenance command that rewrites `minute_ohlcv`'s legacy small chunks into 7-day chunks, one atomic window per transaction, with `--dry-run` planning and a pre-flight that refuses to run unless migration 043 is applied and the minute-family background jobs are paused (Phase A rehearsal proved a concurrent cagg refresh can silently lose materialized rows).

### Added
- **Coverage-aware minute gap-seeding** (slice 162) — the minute daemon now seeds `data_gaps` only for trading sessions genuinely missing from `minute_ohlcv`, instead of a single full-history span. A restart on a mostly-complete universe now produces near-zero chunks per already-covered symbol instead of ~69 (the credit-burning behavior that had the production minute daemon stopped). Adds seed-phase progress logging (`minute seed: N/<total> symbols scanned, M gap rows seeded`) so the daemon no longer runs silent during a universe-wide seed pass.
- **TimescaleDB columnar compression** (slice 160) — migration `042_enable_columnar_compression` enables compression on `minute_ohlcv` and `daily_ohlcv` (segmentby=symbol, orderby=time DESC), installs 7-day compress-after policies, and backfills all existing eligible chunks. Production `minute_ohlcv` achieved 87.7% space savings (10× ratio). All queries return identical results post-compression; cagg refresh policies are unaffected.

### Fixed
- **Minute coverage-index date/datetime type mismatch** (slice 162) — `build_minute_coverage_index` stored the coverage day as a `timestamptz` instead of a plain date, so the session-diff comparison never matched and every symbol was treated as fully uncovered. Fixed before the daemon was restarted in production.

### Changed
- **`MINUTE_HISTORY_MONTHS` NFR removed from architecture docs** (slice 162) — the documented 24-month minute-history cap was a dead AlphaVantage-era workaround, never implemented in code. Minute history is, and remains, full-to-`EODHD_INTRADAY_HORIZON` (2004-01-01) by default, narrowable via `MT_MINUTE_HISTORY_START`.

### Added (previous)
- **`mt serve`** — new CLI command that starts the Data Serving API
  (FastAPI + uvicorn). Defaults: `--host 0.0.0.0 --port 8100`; pass
  `--reload` for dev mode. The server exposes `GET /api/v1/health`,
  which returns `{"status":"ok","db":"ok"}` (HTTP 200) when the
  TimescaleDB connection is live and `{"status":"ok","db":"error",
  "detail":"..."}` when the DB is unreachable (still HTTP 200 — DB
  state is reported in the body, not the status code). Swagger UI is
  available at `/docs`. Requires `MT_TIMESCALE_DB_URL` to be set;
  startup fails fast with `RuntimeError` otherwise. First slice of
  initiative 180 (Data Serving API); bar / symbol / gap endpoints
  land in slices 182–184. (slice 181)
- **`manta_trading.api_server`** Python package — FastAPI app
  factory, lifespan-managed `ConnectionPool` (min=2, max=8), and the
  `get_db()` dependency. Distinct from the existing
  `manta_trading.api` package, which holds outbound provider HTTP
  utilities.
- **`GET /api/v1/bars/{symbol}`** — OHLCV bar endpoint on the Data
  Serving API. Query parameters: `granularity` (any `Granularity`
  value, e.g. `1m`, `1d`), `start`, `end` (ISO dates), `adjusted`
  (bool, default `true`), `format` (`json` or `msgpack`). Minute
  granularities (`1m`, `5m`, `15m`, `1h`, `4h`) route to the minute
  DB; all others route to the daily DB. Returns `{"symbol", "granularity",
  "adjusted", "count", "bars": [...]}`. Unknown symbol or empty range
  returns `404 {"error": "..."}`. (slice 182)
- **`GET /api/v1/symbols`** — list instruments. Optional `?search=<prefix>`
  for case-insensitive prefix match. Returns `{"symbols": [...], "count": N}`
  where each symbol entry includes `symbol`, `exchange`, `type`,
  `asset_class`, and `active` (derived from `NOT delisted_at_eodhd`).
  No pagination — full universe is ~36k rows of small metadata. (slice 183)
- **`GET /api/v1/symbols/{symbol}`** — full instrument metadata plus
  available data ranges per granularity. `available` is a dict keyed by
  granularity string (e.g. `"1d"`, `"1m"`) with `{"start": "YYYY-MM-DD",
  "end": "YYYY-MM-DD"}` values. Ranges are computed lazily via two indexed
  queries (`minute_5min_ohlcv` for all minute granularities; `daily_ohlcv`
  for all daily granularities) — sub-millisecond at single-symbol scope,
  no materialized view needed. Granularities with no data are omitted from
  `available`. Unknown symbol returns `404 {"error": "..."}`. (slice 183)
- **`GET /api/v1/gaps/{symbol}`** — read data gaps from the `data_gaps`
  table. Optional `?granularity=` (any `Granularity` token; `1m`/`5m`/etc.
  map to DB family `"minute"`, `"1d"`/etc. map to `"daily"`), `?start=` and
  `?end=` (ISO dates) for overlapping-interval window filtering. Returns
  `{"symbol", "count", "gaps": [...]}` with each gap carrying `gap_start`,
  `gap_end`, `granularity`, `fetch_status`, `attempt_count`,
  `last_attempt_ts`. Unknown symbol or symbol with no gaps returns
  `200 {"count": 0, "gaps": []}` — not 404. (slice 184)
- **Global 500 exception handler** — unhandled exceptions in any API route
  now return `500 {"error": "internal server error"}` with a sanitized body.
  Full traceback is logged server-side via `logger.exception`. Covers all
  existing routes without per-route changes. (slice 184)
- **`mt serve --workers N`** — new option on `mt serve` to specify the
  number of uvicorn worker processes (default 1). (slice 184)
- **OpenAPI description** — `GET /docs` now shows the API description
  "Data serving API for OHLCV bars, symbol metadata, and gap status."
  alongside the existing title and version. (slice 184)

- **`manta_trading.data.equity_universe`** — new module providing
  `equity_universe(conn, as_of_date, universe=None) -> list[str]`, a
  survivorship-bias-free symbol filter for backtesting. Active-on-date
  semantics use `COALESCE(first_listing_date, first_data_date)` as the
  lower bound and `delisted_date` as the upper bound. When `universe`
  is given (e.g. `'sp500'`), the result is intersected with
  `universe_members` membership as of `as_of_date`, honouring
  `added_date`/`removed_date`. Raises `UniverseQueryError` for unknown
  universe names. Both DB query paths use index scans and complete in
  <10ms on prod (~31k instruments, ~2700 SP500 change rows). (slice 130)

- **`mt data pull --include-delisted`** — new flag that expands `--universe`
  to include delisted instruments in addition to active ones. Requires
  `--universe`; passing it without `--universe` exits 1 with a clear error.
  Default `--universe` behaviour is now tightened to active-only
  (`delisted_at_eodhd = FALSE AND delisted_date IS NULL`), excluding
  in-flight delisted symbols that the daemon's `iter_active_instruments`
  previously included. (slice 158)

## [0.4.0] - 2026-05-09

### Added
- **`mt data init`** — single-command cold-start for a fresh TimescaleDB
  database. Applies all pending schema migrations and prints a status
  summary. Idempotent. Replaces the deleted
  `python -m manta_trading.market.timescale_init` invocation. Flags:
  `--validate-only` (inspect without applying), `--yes` (reserved),
  `--json`. (slice 156)
- **Cold-start integration test** at `test/integration/test_cold_start.py`.
  Spins up an ephemeral UUID-named database for each test, applies the
  full migration chain, and asserts every expected table, cagg, and
  refresh policy exists. A negative test removes
  `038_create_acquisition_state` and asserts that the chain fails with a
  clear error on `019_slim_acquisition_state` — guarding against the
  exact regression class that issue #16 reported. Requires
  `MT_TIMESCALE_TEST_URL` (admin connection); see README. (slice 156)

### Fixed
- **Cold-start regression (issue #16).** A fresh `trading` database
  could not be migrated: migration `019_slim_acquisition_state` failed
  with `UndefinedTable: relation "acquisition_state" does not exist`
  because slice 152's demolition deleted the original CREATE without
  replacement. Migration `038_create_acquisition_state` (idempotent
  CREATE TABLE IF NOT EXISTS, post-030 column shape, inserted by
  list-position immediately before 019) restores the table on fresh
  DBs and is a no-op on existing ones. (slice 156)
- **`daemon_heartbeat` table missing on cold-start DBs.** Surfaced as
  a follow-up to issue #16: `HeartbeatStore` references
  `daemon_heartbeat` but no migration created it; `trading_test` had
  it from an ad-hoc creation predating the chain. New migration
  `039_create_daemon_heartbeat` (idempotent CREATE TABLE IF NOT
  EXISTS) folds it in. The cold-start integration test's expected-
  tables manifest now includes `daemon_heartbeat` so the regression
  class cannot recur silently. (slice 156)
- **Migration 036 used `Connection.executemany` (psycopg2 API).**
  Latent psycopg3-port bug: `_copy_splits_dividends_from_marketdb`
  called `executemany` on a `Connection` object, which raises
  `AttributeError` in psycopg3 (the method only exists on `Cursor`).
  Never triggered in production because cold-start runs typically had
  `MT_MARKET_DB_URL` unset (the no-op path). Fix wraps the inserts
  in a `with conn.cursor() as cur:` block. The cold-start integration
  test now (a) forcibly unsets `MT_MARKET_DB_URL` so it stays
  hermetic regardless of the developer's environment, and (b) gains
  an opt-in test gated on `MT_MARKET_DB_URL_FOR_COLD_START_TEST` that
  exercises the live-MarketDB code path so this bug class is caught
  next time. (slice 156)
- **`mt data caggs status` no longer crashes on never-materialized
  caggs.** `_timescaledb_functions.cagg_watermark()` returns a
  microsecond sentinel (e.g. `-210866803200000000` = `4714-11-24 BC`)
  for empty caggs; the previous code piped that into `to_timestamp()`
  and crashed psycopg's TimestamptzLoader. The status command now
  detects pre-AD-1 watermarks and reports `mat_latest = —`. (slice 156)

### Changed
- **`timescale_init.py` deleted; migration list is the sole source of
  schema truth.** Four new front-of-list migrations
  (`001a_create_timescaledb_extension`,
  `001b_create_minute_ohlcv`,
  `001c_create_minute_ohlcv_hypertable` with 4-hour chunks,
  `001d_create_minute_ohlcv_indexes`) replace the standalone init
  module. Each is idempotent against existing databases. Verified
  byte-identical schema parity with `trading_test` for all 13 tables
  the chain creates. (slice 156)

## [0.3.3] - prior

### Added
- **`mt data get <symbol> <granularity>`** — read OHLCV bars for any supported granularity token (`1m 5m 15m 1h 4h 1d 1w 1mo 1q`). Routes to `TimescaleDailyDataDB` for daily/coarser tokens and `TimescaleMinuteDataDB` for sub-daily. Adjusted by default; use `--raw` for unadjusted prices. Output: Rich table (default), `--json`, or `--csv`. (slice 154)
- **`mt data pull <granularity>`** — fetch or verify data gaps for a set of symbols. Granularity accepts `1d` or `1m` only. Symbol selection: `--symbol`, `--symbols`, `--list NAME`, or `--universe`. Modes: default (fetch UNKNOWN gaps), `--verify` (report only), `--reset` (reset terminal gaps before fetch), `--dry-run` (preview without changes). Replaces `mt data daily update*`, `mt data minute update*`, and `mt data refetch`. (slice 154)
- **`mt data caggs refresh / status`** — manage continuous aggregates manually. `caggs refresh` calls `CALL refresh_continuous_aggregate(...)` for all 7 caggs (or a `--granularity` subset). `caggs status` shows last refresh time, policy schedule, and row counts. (slice 154)
- **`DailyMode` StrEnum** in `manta_trading.constants` — `BACKFILL` vs `STEADY_STATE` for daemon daily-cycle mode selection. (slice 154)
- **Daemon bulk-EOD steady-state path** — when all scope members are caught up (no UNKNOWN daily gaps), the daemon issues one `/eod-bulk-last-day/US` call instead of per-symbol `/eod` calls, reducing credit usage. Falls back to per-symbol BACKFILL mode if any symbol has UNKNOWN gaps. (slice 154)

### Removed
- **`mt data daily *` commands removed** — `daily update`, `daily update-all`, `daily update-file`, `daily verify`, `daily coverage`, `daily migrate`, `daily symbols`. Use `mt data pull 1d` instead. (slice 154)
- **`mt data minute *` commands removed** — `minute update`, `minute update-all`, `minute backfill`, `minute status`, `minute metrics`. Use `mt data pull 1m` instead. (slice 154)
- **`mt data refetch` removed** — use `mt data pull --reset` instead. (slice 154)

### Added
- **Adjusted prices on read.** `TimescaleDailyDataDB.get_daily_data(adjusted=True)` and `TimescaleMinuteDataDB.get_minute_data(adjusted=True)` (both default) return split- and dividend-adjusted OHLC bars. Volume is unchanged. Pass `adjusted=False` for raw prices. The `adjusted()` function is also importable directly from `manta_trading.data.adjustment`. (slice 153)
- **`Granularity` StrEnum** in `manta_trading.constants` — canonical single-source tokens `M1`, `M5`, `M15`, `H1`, `H4`, `D1`, `W1`, `MO1`, `Q1` with a `GRANULARITY_SOURCE` mapping to the backing table or cagg name. (slice 153)
- **`TimescaleDailyDataDB`** at `manta_trading.market.timescale_daily_db` — reads daily and coarser (weekly, monthly, quarterly) OHLCV bars from `daily_ohlcv` and the daily caggs. Minute-grain tokens raise `ValueError`. (slice 153)
- **Splits and dividends now in TimescaleDB.** Corporate-action tables migrated out of MarketDB. One database, one connection URL (`MT_TIMESCALE_DB_URL`). (slice 152)
- **4 new minute caggs (raw projection).** `minute_5min_ohlcv`, `minute_15min_ohlcv`, `minute_hourly_ohlcv`, `minute_4hour_ohlcv` replace the old 11 legacy caggs (including `_v2` variants). Raw OHLCV projection; no adjusted columns. Refresh policies installed. (slice 152)
- **3 new daily caggs.** `daily_weekly_ohlcv`, `daily_monthly_ohlcv`, `daily_quarterly_ohlcv` over `daily_ohlcv`. (slice 152)

### Changed
- **Adjusted-on-read (architecture change).** `adj_open`, `adj_high`, `adj_low`, `adj_close`, `k_factor`, `adjusted_at` columns dropped from `daily_ohlcv` and `minute_ohlcv`. Adjusted prices are computed on read in slice 153. The daemon no longer writes adjusted columns or performs CA-drift recomputes. (slice 152)

### Removed
- **MarketDB removed.** `MT_MARKET_DB_URL` is no longer used. The `marketdb.py`, `symbol_list_manager.py`, and `instrument_seed.py` modules are deleted. `mt data daily migrate`, `mt data daily symbols`, `mt data daily coverage`, `mt data daily verify`, `mt data daily update*` commands no longer function (replaced in slice 154 by `mt data pull`). (slice 152)
- **AlphaVantage removed.** All AV code, config fields (`alphavantage_api_key`), provider profiles, and tests deleted. EODHD is the sole OHLCV provider. (slice 152)
- **Backtest scaffold removed.** `src/manta_trading/backtest/` deleted entirely. (slice 152)
- **Adjustment audit/drift machinery removed.** `band_writer.py`, `verify.py`, `verify_eod.py`, `audit.py`, `context.py`, `ca_drift.py` deleted. `ADJUSTMENT_DRIFT_EPSILON` constant removed. (slice 152)

### Added
- **`mt data refetch --symbol X`** — operator escape valve for re-fetching a symbol's data window directly from EODHD. Resets terminal gap rows (`PROVIDER_HOLE` / `RETRY_EXHAUSTED`) to `UNKNOWN` so the daemon can retry them. Use `--daily` / `--minute` to scope by granularity; `--from` / `--to` to narrow the date window. Preview what would change with `--dry-run` (no provider calls, no DB writes). Skip the confirmation prompt with `--yes` or `--json`. (slice 148)
- **`mt data status`** — see the health of every symbol in one table. Colored by status (`OK` / `GAPS` / `STALE` / `FAILED`); footer always shows full-universe counts. Drill into a specific symbol with `--symbol X` to see a detail panel and its full gap listing. Get machine-readable output with `--json` for scripting, alerting, or piping into `jq`. (slice 147)
- **Trading calendar horizon stays current automatically** — running `mt data status` or the long-running daemon extends the `trading_sessions` horizon when needed, so you no longer have to remember `mt data extend` for day-to-day operation. The manual command stays for CI and explicit control. (slice 147)
- **`mt data daemon run`** — keep the daemon running continuously. Run it once and it handles daily bars, minute bars, and corporate-action updates on its own schedule. Use `--symbols X,Y` or `--list NAME` to scope it to specific symbols; add `--stop-when-done` to exit when the scope is fully up to date. (slice 146)
- **Named symbol lists** — define groups of symbols in `config/symbol-lists.yaml` and reference them by name in any command that accepts `--list NAME`. Ships with `priority1` (10 core symbols) and `priority2` (S&P 500). Manage with `mt data lists ls / show / refresh-sp500`. (slice 146)
- **`mt data ca update`** — fetch and store splits and dividends. Without flags: pulls yesterday's full US market in one bulk request (200 credits). With `--symbol X` or `--list NAME`: full history per symbol (2 credits each). With `--since N` or `--since YYYY-MM-DD`: trailing window bulk fetch. (slice 146)
- **`mt data ca show --symbol X`** and **`mt data ca list`** — view stored splits and dividends. `show` scopes to one symbol; `list` shows all (capped at 1000 rows). Both accept `--from` / `--to` date filters. (slice 146)
- **Adjusted prices stay correct automatically** — when EODHD's corporate-action data changes for a symbol, the daemon detects the change and recomputes adjusted prices in the background without any manual intervention. (slice 146)
- **EODHD API rate limiting** — all outbound EODHD calls are automatically throttled to stay within your plan limits (1000 calls/min burst, 100k calls/day). The daemon will slow down or pause rather than trip the upstream rate limit. (slice 146)
- **`mt data daemon daily [--symbols X,Y,Z]`** — fetch the latest daily bars for one or all symbols and exit. (slice 145)
- **`mt data daemon minute [--symbols X,Y,Z]`** — fetch missing minute bars for one or all symbols and exit. (slice 145)
- **Automatic gap detection** — the daemon tracks exactly which date ranges are missing or failed and retries them. Each symbol's backfill state is visible in `data_gaps`. (slice 145)
- **`mt data extend [--calendar X] [--strict]`** — extend the trading-sessions horizon so the daemon can correctly identify trading days up to two years out. (slice 144)

### Removed
- **`mt data daemon daily`** and **`mt data daemon minute`** — use `mt data daemon run` instead. It does everything both commands did, plus CA updates, plus it keeps running. (slice 146)
- **`mt data adjustment ingest/verify/verify-against-eodhd-eod`** — use `mt data ca update` / `mt data ca show` instead. (slice 146)
- **`mt data daily daemon`** and **`mt data minute daemon`** (long-running foreground daemons) — superseded by `mt data daemon daily/minute`. (slice 145)

### Added
- **`trading_sessions` table** (migration `025_trading_sessions_table`) — materializes per-(`calendar_id`, `session_date`) RTH session bounds (`session_open_utc`, `session_close_utc`). One row per trading day (weekends and `market_status='closed'` holidays absent by construction). Primary key on `(calendar_id, session_date)`; supporting index on `(calendar_id, session_close_utc)` for the new view CTE. Single source of truth for both the rewritten SQL view and the Python `TradingCalendar` class — eliminates the dual-implementation risk that existed when SQL and Python independently computed session bounds. (slice 144)
- **Initial trading_sessions population** (migration `026_trading_sessions_initial_population`) — populates rows for every calendar in `trading_calendars` from the earliest seeded holiday year through `current_year + TRADING_SESSIONS_EXTENSION_YEARS` (default 2 years forward). Idempotent via `ON CONFLICT (calendar_id, session_date) DO UPDATE`. Migration runner now supports a `python_fn` callable in addition to `sql` strings, so the population can call `populate_trading_sessions` directly rather than reimplementing weekend/holiday/timezone logic in SQL. (slice 144)
- **`mt data extend [--calendar X] [--strict]`** — operator command extending the `trading_sessions` horizon for one or all calendars. Idempotent (re-running a fully extended calendar reports `0 sessions inserted`). With `--strict`, exits non-zero (code 4) if any calendar's `MAX(session_date)` is within `TRADING_SESSIONS_HORIZON_WARN_DAYS` (default 90) of today — wire it to a CI / cron alert to catch horizon exhaustion before the daemon does. (slice 144)
- **`populate_trading_sessions(calendar_id, start_date, end_date, calendars_row, holidays_rows) -> list[dict]`** — pure function in `manta_trading.data.base.session_population` that generates the row set for a given range. Same algorithm as `TradingCalendar._build_trading_hours` (skip weekends, skip closed holidays, apply `early_close_time` / `late_open_time` overrides, convert to UTC) — both consumers (migration 026 / `mt data extend` / `_build_trading_hours` RTH path) call this single implementation. (slice 144)
- **`OutOfHorizonError`** — raised by `TradingCalendar.is_trading_day` and `get_trading_hours(date, RTH)` when the requested date is past the populated horizon. Carries `calendar_id`, `date`, and `horizon_end`; the message names the maintenance command (`mt data extend`). Fail-loud rather than silent fallback to inline computation. (slice 144)
- **`TRADING_SESSIONS_EXTENSION_YEARS`** (default 2) and **`TRADING_SESSIONS_HORIZON_WARN_DAYS`** (default 90) — new constants in `manta_trading.constants`. (slice 144)
- **`daily_ohlcv` hypertable** (migration `023_daily_ohlcv`) — TimescaleDB hypertable mirroring `minute_ohlcv`'s column shape (OHLCV + `adj_*`, `k_factor`, `adjusted_at`, `created_at`), `chunk_time_interval = '7 days'`, unique index on `(symbol, time)` plus covering indexes for symbol-range and time-range scans. Prerequisite for slice 144's bulk daily-history backfill. After migration `024_data_status_view_refresh`, the `data_status` view automatically picks up both the daily and minute branches. (slice 143)
- **`compute_snapshot_id(splits, dividends) -> str`** — stable SHA256 hex digest over canonicalized split/dividend corporate-action identity `(ex_date, ratio_to, ratio_from)` / `(ex_date, amount)`. Cross-process deterministic; `fetched_at` intentionally excluded (the ingest path bumps it on every upsert regardless of CA change). Used by slice 144's daemon to detect when adjustments are stale without spurious recomputes. (slice 143)
- **`CaSnapshot` dataclass** — frozen bundle of `(symbol, splits, dividends, prev_closes, snapshot_id)` returned by `current_ca_snapshot`. `frozen=True` prevents field reassignment; `dict` field makes instances non-hashable by design (no caller uses `CaSnapshot` as a dict key). (slice 143)
- **`current_ca_snapshot(symbol, *, settings) -> CaSnapshot`** — loads splits, dividends, and per-dividend `prev_close` from the daily DB, computes `snapshot_id` at construction time, returns a ready-to-use `CaSnapshot`. Drop-in replacement for the old `load_adjustment_context`. (slice 143)

### Changed
- **`data_status` view** (migration `028_data_status_view_target_end_ts`) — `target_end_ts` now projects from a real `exchange_completed_close` CTE (`MAX(session_close_utc) WHERE session_close_utc + LATE_BAR_GRACE_PERIOD < NOW()` per calendar), replacing the slice-142 `NULL::TIMESTAMPTZ` stub. `LEFT JOIN` on `instruments.trading_calendar_id` so symbols on a calendar with no rows in `trading_sessions` still appear with `target_end_ts = NULL`. Migration 028 branches on `to_regclass('trading_sessions')` so it is safe to apply on a DB where 025/026 didn't land for any reason. (slice 144)
- **`TradingCalendar.is_trading_day(date)`** — now reads `EXISTS(SELECT 1 FROM trading_sessions WHERE calendar_id=? AND session_date=?)` instead of querying `trading_holidays` for `market_status='closed'`. Weekend dates resolve to false via the same EXISTS query (they are absent from `trading_sessions`). Per-instance dict cache preserved. Raises `OutOfHorizonError` for dates past the populated horizon. (slice 144)
- **`TradingCalendar.get_trading_hours(date, RTH)`** — RTH path now reads `session_open_utc`/`session_close_utc` directly from `trading_sessions` and returns a `TradingHours` with timezone-converted local times. ETH / ALL paths unchanged (still derive from calendar metadata + `trading_holidays` overrides — extended hours are not stored in `trading_sessions`). Raises `OutOfHorizonError` for dates past the populated horizon. (slice 144)
- **`compute_k_factor`** — renamed from `k_factor`; accepts either the original positional form `(symbol, target_date, splits, dividends, prev_closes)` or the new `ca_snapshot=` keyword form. All in-tree call sites (minute writer, verify, verify_eod, orchestrator) migrated to `ca_snapshot=`. (slice 143)
- **`data_status` view** (migration `024_data_status_view_refresh`) — re-executes the same branching `DO $$` block as migration 021; on DBs that ran 021 before 023 applied, flips the `bars_summary` CTE to include both `daily_ohlcv` and `minute_ohlcv`. On fresh DBs the re-execution is a safe no-op. (slice 143)

### Removed
- **`k_factor`, `AdjustmentContext`, `load_adjustment_context` deprecated aliases** — removed from `manta_trading.data.adjustment` and its `__init__`. Use `compute_k_factor`, `CaSnapshot`, and `current_ca_snapshot` (the canonical names from slice 143) instead. (slice 144)

---

- **`mt data migrate-cold-start` — schema migration + cold-start command** — single irreversible operation that applies migrations 018-022 (introducing the slimmed `acquisition_state`, the `data_gaps` table, and the `data_status` view) and TRUNCATEs the AV-era bar tables (`minute_ohlcv`, `daily_ohlcv`, `acquisition_state`) in one transaction. Pre-flight verifies slice 141 has run (migrations 015/016/017 present, every `instruments` row has `eodhd_type`, `instruments` count in `[30k, 80k]`, `instruments.active` is gone) and probes EODHD `/eod` for liveness. Operator-friendly gates: 5-second wait + `truncate` confirmation prompt (skipped under `--yes`), `--dry-run` for pre-flight only, `--skip-probe` to bypass EODHD liveness, `--json` for machine-readable output, `-v` for verbose logging. Exit codes: 0 success, 1 pre-flight failed, 2 operator declined, 3 migration/TRUNCATE failed (transaction rolled back). (slice 142)
- **`data_gaps` table** (migration `018_data_gaps`) — `(symbol, granularity, gap_start, gap_end, fetch_status, last_attempt_ts, attempt_count)` with PK on the first four columns and CHECK constraints on `fetch_status` (derived from the new `FetchStatus` StrEnum: `UNKNOWN`, `PROVIDER_HOLE`, `FAILED_RETRYABLE`, `RETRY_EXHAUSTED`), `granularity IN ('daily','minute')`, `gap_end >= gap_start`, `attempt_count >= 0`. Indexes on `(symbol, granularity)` and `fetch_status`. Writers land in slice 144. (slice 142)
- **`data_status` view** (migration `021_data_status_view`) — per-(symbol, granularity) health summary joining `instruments`, `trading_calendar`, bar tables, `data_gaps`, and `acquisition_state`. CASE expression yields `OK`/`STALE`/`GAPS`/`FAILED` from `last_attempt_ts` staleness, `gap_count`, and `BOOL_OR(fetch_status = 'RETRY_EXHAUSTED')`. `LEFT JOIN exchange_completed_close` so unknown-calendar symbols surface as STALE rather than disappear. Stale thresholds rendered from `DAILY_STALENESS_THRESHOLD` (2 days) and `MINUTE_STALENESS_THRESHOLD` (1 day) at migration build time. (slice 142)
- **`manta_trading.constants` module** — single source of truth for `ADJUSTMENT_DRIFT_EPSILON`, `MAX_RETRY_COUNT`, `DAILY_STALENESS_THRESHOLD`, `MINUTE_STALENESS_THRESHOLD`, `DAILY_HISTORY_MONTHS` (None = unbounded), `MINUTE_HISTORY_MONTHS` (24), `LATE_BAR_GRACE_PERIOD` (30 min), `MAX_GAP_STALENESS` (5 min). The freshness helpers and migration view DDL now consume these constants instead of repeating the literals. (slice 142)
- **`LastAttemptOutcome` StrEnum** — `success`, `partial`, `empty`, `transient_failure`. Replaces the legacy `AcquisitionStatus` field on `acquisition_state` rows. CHECK constraint on the new column derived from the enum (migration `022`). (slice 142)
- **`FetchStatus` StrEnum** + **`DataGap` read DTO** — typed read access to the new `data_gaps` table; writers are deferred to slice 144. (slice 142)

### Changed
- **`acquisition_state` table — slimmed shape (migration `019_slim_acquisition_state`)** — drops `last_success_ts`, `retry_count`, `error_message`, `run_id`, `status`; adds `last_attempt_outcome` and `last_adjusted_ca_snapshot_id`. The watermark for "what bars do we have?" now comes from the bar tables themselves; the state row records only the outcome of the last attempt. The orchestrator core, daily/minute orchestrators, the daemon work-queue logic (both daily and minute), and `mt data state` / `mt data daily status` / `mt data minute status` all updated to read the new shape. (slice 142)
- **`AcquisitionResult.final_status`** now uses a new `RunStatus` enum (`OK`/`FAILED`) defined in `data/acquisition/orchestrator.py` instead of the dropped `AcquisitionStatus`. (slice 142)
- **Daemon work-queue retry policy** — the old `retry_count`-driven exponential backoff is replaced by a flat 30-minute time-since-last-attempt window keyed on `last_attempt_outcome == TRANSIENT_FAILURE`. Slice 144 will replace this with `data_gaps.attempt_count`-driven backoff. The `max_retries` parameter is retained as a no-op for backward-compatible callers. (slice 142)

### Removed
- **`mt data minute coverage` command** — its `coverage_gaps` table is dropped by migration `020`. Empirical gap detection moves to slice 144's daemon-driven `data_gaps` workflow; per-symbol gap inspection becomes part of slice 145's `mt data status --symbol X`. (slice 142)
- **`coverage_gaps` table** (migration `020_drop_coverage_gaps`) — including the NVDA inaugural seed row from migration `014`. Slice 144's first NVDA fetch over the 2024-06-07 → 2024-07-25 window will re-derive the row as a `PROVIDER_HOLE`. (slice 142)
- **`src/manta_trading/data/coverage/` package** — `CoverageGapStatus`, `CoverageRow`, `scan_coverage`, `persist_coverage_gaps`, etc. The four `CoverageGapStatus` string values used by historic migrations 012 and 014 are inlined as module-level constants in `migrations/minute.py`. (slice 142)

### Added
- **`mt data instruments rebuild` — universe rebuild from EODHD bulk symbol-list** — populates the instruments table with ~57k US equities + USA indices in three EODHD calls (active US, delisted US, INDX). Idempotent upsert preserves AV-seeded venues (NASDAQ/NYSE/NYSE_ARCA/BATS/NYSE_MKT) and canonical_ids; new rows get a transient `venue='US'` until Finnhub `/stock/profile2` enrichment promotes them to the authoritative exchange. Adds lifecycle columns (`first_listing_date`, `first_data_date`, `delisted_date`, `eodhd_type`, `eodhd_exchange`, `delisted_at_eodhd`) via migration 015; tightens NOT NULL + CHECK in 016; drops the legacy `active` column in 017. The orchestrator runs migrations in two phases (015 before upsert; 016/017 after upsert + orphan delete) so `eodhd_type` is populated before the NOT NULL constraint applies. Orphans (DB rows absent from current EODHD payload) are deleted after a 5-second confirmation gate. `--dry-run`, `--skip-finnhub`, and `--json` flags supported. (slice 141)
- **EODHD daily-OHLCV provider** — `EODHDDailyProvider` implementing `IDailyDataProvider`, fetching from `/eod/{ticker}`. Replaces AlphaVantage as the production daily source after the AV account was cancelled (2026-04-27). Per-row `dividend_amount`/`split_coefficient` columns are written `0.0` (the authoritative values live in the splits/dividends tables — slice 127). 11K+ row history retrieved in one call. Same provider for minute and daily by default; project guidance is "same provider unless a strong reason to differ." (slice 128)
- **`DailyProviderName` StrEnum + `build_daily_provider(settings)`** — daily-provider seam at `data/acquisition/daily/providers/__init__.py`. Mirrors the slice-127 minute-provider seam. Selected via `MT_DAILY_PROVIDER` env var (default `eodhd`; `alphavantage` selectable but not the default). (slice 128)
- **`MT_DAILY_PROVIDER` setting** — default `"eodhd"`. Pydantic-validated against `DailyProviderName`; unknown values fail-fast at config load. (slice 128)
- **Daemon-owned corporate-action ingest** — `mt data daily daemon` (and `mt data daily update`) now ingests splits and dividends per symbol as part of each cycle (OHLCV → splits → dividends → checkpoint). Failures of either CA step are logged and emitted as `ca_ingest_failed` events but do **not** block the OHLCV checkpoint advance; CA staleness is recoverable on the next cycle. Per-step consecutive-failure counter escalates to ERROR-level logging at 3. Replaces the slice-127 manual `mt data adjustment ingest` workflow as the production path; the manual command remains for ad-hoc operator use. (slice 128)
- **`ICorporateActionsProvider` protocol seam** — `src/manta_trading/data/adjustment/providers/__init__.py` introduces the protocol, `CorporateActionsProviderName` StrEnum, and `build_corporate_actions_provider(settings)` helper. Mirrors the slice-127 minute-provider seam. `EODHDCorporateActionsProvider` (extracted from the slice-127 ingest module) is the first implementation; selected by `MT_CORPORATE_ACTIONS_PROVIDER=eodhd` (default). Adding a future Polygon/AlphaVantage CA provider is a 3-line dispatch change. (slice 128)
- **Provider-error classification** — `ProviderTransientError` and `ProviderPermanentError` added to `manta_trading.providers.errors`. The shared HTTP retry helper at `data/adjustment/providers/_http.py` classifies failures and applies exponential backoff (1s/2s/4s, capped 60s, max 3 retries) for transient cases; honors `Retry-After` on 429. Used by both `EODHDCorporateActionsProvider` and the Stage B verifier. (slice 128)
- **`mt data minute coverage --symbol|--all --from --to [--threshold N] [--json] [--persist]`** — per-day coverage scan against the trading calendar. For each `(symbol, trading_date)` in scope compares stored bar count on `minute_ohlcv` against the calendar's expected count (early-close aware via `TradingCalendar.get_expected_bar_count`). Default threshold = 80% of expected; `--threshold` overrides. With `--persist` (always with `--all`) replaces gap-range entries in `coverage_gaps` for the scanned (symbol, source, date-range) using DELETE-then-INSERT — handles gap shrink, split, and merge correctly. Exit 0 if all-present, 1 if any gap or partial. Provider-agnostic (operates on stored data only). (slice 128)
- **`coverage_gaps` table** on the TimescaleDB host (migration `012_coverage_gaps`) — schema `(symbol, gap_start, gap_end, source, detected_at, resolution_status, notes)` with PK `(symbol, gap_start, source)` and CHECK constraint enumerating valid `resolution_status` values from the `CoverageGapStatus` StrEnum. Indexes on `symbol` and `resolution_status`. (slice 128)
- **NVDA inaugural `coverage_gaps` row** (migration `014_nvda_inaugural_gap`) — documents the EODHD-acknowledged 2024-06-07 → 2024-07-25 gap (`provider_confirmed_unfillable`). Idempotent via `ON CONFLICT DO NOTHING`. (slice 128)
- **`mt data adjustment verify-against-eodhd-eod --symbol --from --to [--tolerance F] [--json]`** — Stage B verifier. Fetches EODHD `/eod/{symbol}` once for the range, computes `published_k = adjusted_close / close` per day, compares against the stored `k_factor` from `minute_ohlcv`. Tolerance default 0.0001 absolute. Exit 0 = all pass, 1 = any FAIL, 2 = provider error. Quota cost: 1 EODHD call per invocation. Provider-coupled by design — recorded in the adjustment ADR. (slice 128)
- **`mt data minute backfill --universe NAME --since DATE [--max-symbols N] [--quota-fraction F]`** — universe-scale minute backfill. Resumes from `backfill_state.cursor_symbol`. Drives the steady-state minute-update path so `acquisition_state` watermarks advance (the regular minute daemon resumes cleanly afterward). Soft daily-quota guard (default 80% of `MT_EODHD_DAILY_LIMIT = 100K`); when the cap is hit, sleeps until the next UTC midnight and emits `quota_sleep` + `quota_window_advance` events. SIGINT/SIGTERM stops cleanly after the current symbol. **Operationally exclusive with the minute daemon** (Decision 18) — runbook stops the daemon before invoking. (slice 128)
- **`backfill_state` table** on the TimescaleDB host (migration `013_backfill_state`) — universe-iteration cursor and per-day quota window: `(universe PK, cursor_symbol, since_date, started_at, last_progress_at, daily_calls_used, daily_calls_window_start)`. (slice 128)
- **Backfill section on `mt data minute status`** — when one or more `backfill_state` rows exist, the status command renders a "Backfill:" section showing per-universe cursor, since_date, last_progress timestamp, and `quota=used/cap (pct%)`. JSON output gains a top-level `backfill` array; empty when no rows. (slice 128)
- **New event types** in `AcquisitionEventType`: `CA_INGEST_SPLITS`, `CA_INGEST_DIVIDENDS`, `CA_INGEST_FAILED`, `VERIFY_EOD`, `BACKFILL_SYMBOL`, `QUOTA_SLEEP`, `QUOTA_WINDOW_ADVANCE`. Flow through the existing `JsonlEventSink` (slice 121). (slice 128)
- **systemd unit templates** at `deploy/systemd/mt-daily-daemon.service.tmpl` and `mt-minute-daemon.service.tmpl` — `User=Group=${MANTA_TRADING_SERVICE_USER}` (envsubst-rendered at install), `EnvironmentFile=/etc/manta-trading.env`, `Restart=on-failure`, `StartLimitBurst=5/300s`, hardening flags (`NoNewPrivileges`, `ProtectSystem=full`, `ProtectHome`, `PrivateTmp`). (slice 128)
- **journald drop-in** at `deploy/systemd/journald-manta-trading.conf` — `SystemMaxUse=2G`, `SystemMaxFileSize=200M`. (slice 128)
- **Production runbook** at `project-documents/user/runbooks/production-deploy.md` — gated by Phase 0 (PM-confirmed minute-data backup, explicit timestamp recorded) and Phase 1 (≥24h test-environment dry-run evidence). Covers install, gated daily/minute service start, failure-recovery sanity check, 24h log-volume check, and optional gated backfill phase with the explicit `systemctl stop mt-minute-daemon` prerequisite. (slice 128)
- **`MT_EODHD_DAILY_LIMIT`** setting (default `100_000`) — drives the slice-128 backfill quota guard. (slice 128)
- **EODHD minute data provider** — replaces AlphaVantage as the source of historical 1-minute bars. Single 120-day chunk per fetch (76,000+ bars in ~1.3s on the paid tier), 22-year history depth. Plan ceilings: 1,000 req/min burst, 100,000 credits/day; intraday call costs 5 credits. Selected via `MT_MINUTE_PROVIDER=eodhd` (default) and `MT_EODHD_API_KEY`. (slice 127)
- **Split/dividend adjustment layer** — `minute_ohlcv` gains six new columns (`adj_open`, `adj_high`, `adj_low`, `adj_close` `NUMERIC(20,8)`; `k_factor` `NUMERIC(20,12)`; `adjusted_at TIMESTAMPTZ`) populated atomically with raw OHLCV in the same writer transaction. Adjusted prices are recomputable from the persisted corporate-actions ground truth, never trusted blindly from a provider. (slice 127)
- **Splits and dividends tables** on the daily DB (`splits`, `dividends`) with PK `(symbol, ex_date)` and provenance columns (`source`, `fetched_at`). Hold the corporate-action history that drives the k-factor formula. (slice 127)
- **`mt data adjustment ingest --symbol SYMBOL [--since DATE]`** — fetch and upsert splits and dividends for a symbol from EODHD. One API call to `/splits/{ticker}` plus one to `/div/{ticker}`. Idempotent: re-running upserts in place via `ON CONFLICT (symbol, ex_date) DO UPDATE`. (slice 127)
- **`mt data adjustment verify --symbol [--from] [--to] [--tolerance] [--json]`** — operator's confidence signal that the adjustment layer is internally consistent. Recomputes the expected k-factor from the current corporate-actions tables and compares to the stored `adj_close`; per-day rollup with PASS/FAIL status; non-zero exit code if any day exceeds tolerance. Default tolerance `0.0001` absolute price units. (slice 127)
- **`mt data minute update SYMBOL --from DATE --to DATE`** — ad-hoc backfill mode on the existing update command. Fetches the explicit window without touching `acquisition_state`, leaving the production resumable path unaffected. Useful for filling specific historical windows for tests, strategy backfill, or one-off catch-up. (slice 127)
- **`UNIQUE (symbol, time)` index on `minute_ohlcv`** — migration `011_minute_ohlcv_unique_symbol_time` deduplicates existing rows (latest `created_at` wins) and adds the unique index; the writer's COPY-into-staging-then-`INSERT...ON CONFLICT (symbol, time) DO NOTHING` path now silently skips duplicates rather than producing them. Brings the minute path in line with the daily path's long-standing dedup behavior. (slice 127)
- **`AdjustmentContext` + `load_adjustment_context`** — pure data structure bundling a symbol's splits, dividends, and prev_closes; the orchestrator pre-loads it once per symbol-update job and hands it to the writer. (slice 127)
- **`k_factor(symbol, target_date, splits, dividends, prev_closes)`** — pure deterministic Decimal function computing the cumulative price-adjustment multiplier; matches EODHD's published `adjusted_close / close` ratio to ~5e-9 in practice. (slice 127)
- **ADR `120-arch-adjustment-policy.md`** — durable record of the storage / ground-truth / k-factor / verification design decisions and alternatives considered. (slice 127)
- **`mt data migrate apply [--db minute|daily|all] [--json]`** — apply pending schema migrations to one or both databases. Default `--db all` runs both tracks; if one URL is unconfigured it is skipped with a warning; requesting a specific unconfigured track exits non-zero. Prints per-track applied IDs and a summary line. `--json` emits `{"tracks": {"minute": {"applied": [...]}, "daily": {"applied": [...]}}}`. (slice 150)
- **`mt data migrate status [--db minute|daily|all] [--json]`** — show applied and pending migrations per database track as a Rich table (`ID | Status | Description | Applied At`). `--json` includes `connected` flag per track so callers can distinguish a connection failure from an empty-but-reachable DB. (slice 150)
- **`mt data daily verify`** — verify the daily OHLCV database schema is accessible (previous behavior of `mt data daily migrate`; renamed for clarity). (slice 150)
- **Daily-DB migration track** — `MarketDB` now participates in the unified migration framework with a 2-entry daily track (`001_schema_migrations` + `002_reconcile_existing_schema`). Running `mt data migrate apply --db daily` once brings an existing daily DB under managed migration tracking without touching any data. (slice 150)
- **`src/manta_trading/market/schema/runner.py`** — standalone `apply_migrations(pool, migrations)` and `list_migration_state(pool, migrations)` functions. Both DB classes now delegate to this shared runner rather than duplicating logic. (slice 150)
- **`src/manta_trading/market/schema/migrations/`** package — minute track promoted to `minute.py` (`MINUTE_MIGRATIONS`), daily track added as `daily.py` (`DAILY_MIGRATIONS`), `__init__.py` exposes `TRACKS` dict plus backward-compat `MIGRATIONS` alias. `README.md` states the single-source-of-truth rule. (slice 150)

### Changed
- **AlphaVantage no longer the daily provider in production.** `_create_daily_orchestrator` now dispatches via `build_daily_provider(settings)`. `_validate_credentials` validates the EODHD key when the daily provider is EODHD (the default), the AV key when it's AlphaVantage. `MT_ALPHAVANTAGE_API_KEY` becomes optional. `acquisition_state` rows for the daily granularity are now keyed by `provider="eodhd"`; existing AV-keyed rows from prior installs are orphaned (harmless — operator may run a one-line SQL UPDATE if reuse is desired). The runbook env-file template drops the AV key. (slice 128)
- **`mt data minute coverage` retired the slice-101 fleet-summary shape.** The command now requires `--from`/`--to` and produces per-day classification + `coverage_gaps` persistence. The previous read-only fleet/per-symbol inventory (with the >3-day calendar-gap heuristic and TimescaleDB compression info) is removed; if the inventory or compression view is wanted back, ship as a distinct command (`mt data minute compression` is reserved). (slice 128)
- **`TradingCalendar` schema-column mismatch fixed.** The class queried `calendar_name`/`market_open_time`/`market_close_time` against a schema that uses `exchange_name`/`market_open`/`market_close`. The select now aliases the actual columns to the historical attribute names so existing callers are unchanged. Surfaced because slice 128 is the first consumer that calls `is_trading_day()` against a real DB. (slice 128)
- **Adjustment ADR** (`120-arch-adjustment-policy.md`) updated with three new sections: provider compatibility contract (which capabilities a provider stack must supply), outlier-handling Non-Goal (no smoothing/ffill/Z-score deletion at storage time, ever), and Stage B coupling rationale (the verifier is named after its source by design). (slice 128)
- **Initiative 140 architecture doc** (`140-arch.data-quality-operations.md`) gains a "Slice 128 handoff" section enumerating the operational baseline 140 inherits (coverage scanner, Stage B verifier, event types) and the analytical extensions explicitly out of scope for slice 128. (slice 128)
- **Slice 126 marked `superseded` by 128.** The systemd template and runbook structure are reused; the rest is rewritten for the post-127 EODHD reality. (slice 128)
- **`IMinuteDataProvider` protocol** — gains `max_days_per_request: int` so each provider declares its own window. The orchestrator's chunk-range computation reads this and emits per-provider chunks (EODHD: 120 days, AV: 30 days). Replaces the calendar-month-only `_compute_month_ranges`. (slice 127)
- **Minute writer transaction shape** — was a single bare `COPY` into `minute_ohlcv`; now COPYs into a per-connection TEMP staging table (carrying both raw and adjusted columns) and INSERTs into the live hypertable with `ON CONFLICT (symbol, time) DO NOTHING`. Single transaction; raw + adjusted writes are atomic. (slice 127)
- **Third-party logger levels** — `httpx` and `httpcore` loggers are pinned to WARNING in `setup_logging` so their default URL-emitting INFO output cannot leak `api_token=…` query parameters. Affects every httpx caller in the project. (slice 127)
- **`mt data daily migrate`** — repurposed from a no-op `verifyDatabase()` wrapper to a real migration runner. Now calls `MarketDB.apply_schema_migrations()` (same as `mt data migrate apply --db daily`). Use `mt data daily verify` for the old schema-check behavior. (slice 150)
- **`mt data migrate`** is now a sub-app with `apply` and `status` subcommands. The old `mt data migrate` (which ran only the minute track) is replaced by `mt data migrate apply` (defaults to `--db all`). (slice 150)

### Fixed
- **EODHD null-volume bars no longer crash the minute fetch** — EODHD emits `volume: null` on indicative pre-market bars (windows where no trades occurred but a quote-update / imbalance produced a synthesized OHLC observation). The provider now coerces `null → 0` and logs the count at INFO so the rate is observable. Volume = 0 is semantically accurate; strategy backtests that need executable bars should filter `volume > 0`. (slice 127)

### Removed
- **`instruments.active` column** — replaced by the lifecycle pair `delisted_at_eodhd = FALSE AND delisted_date IS NULL`. `Instrument.active` removed from the dataclass; query predicates and the `mt data instruments list` "Active" column updated to "Listed" derived from the new boolean. Migration 017 drops the column after consumer code is updated. (slice 141)
- **`MinuteCoverageAnalyzer` and `src/manta_trading/market/timescale_minute_coverage.py`** — slice-101 module deleted along with the old `mt data minute coverage` shape. Superseded by `data/coverage/scanner.py` which is calendar-aware, threshold-configurable, and persists to `coverage_gaps`. The deleted unit-test file is replaced by `test/unit/test_coverage_scanner.py` and `test/integration/test_coverage_persistence.py`. The `TimescaleMinuteDataDB.get_fleet_summary`/`detect_gaps`/`get_daily_bar_counts` methods remain (used only by tests) and are candidates for cleanup in a future slice. (slice 128)
- **AlphaVantage minute provider unwired from runtime** — `mt data minute update` and the minute daemon now dispatch through `build_minute_provider` to EODHD. The `AlphaVantageMinuteProvider` file is preserved on disk as 'dormant' (not deleted) per implementer choice; AV-minute unit tests still pass standalone. `MT_ALPHAVANTAGE_API_KEY` remains required — the daily AV provider continues to ship and is unrelated. (slice 127)
- **`database/migrations/*.sql`** and **`sql/01_setup_database.sql`** / **`sql/02_setup_verification.sql`** — orphaned SQL files from archived Slice 750 work, not referenced by any runner or tracked in any DB. Git history preserves them. (slice 150)

### Added
- **`mt data minute daemon [--poll-interval N] [--max-retries N] [--requests-per-minute N]`** — long-running foreground daemon that continuously cycles through active instruments, keeping minute OHLCV data current. Supports graceful SIGTERM/SIGINT shutdown, exponential backoff for failed symbols, interruptible sleep when caught up. `UNFILLABLE` symbols (AV 24-month cutoff) are permanently excluded from the work queue and never retried. `--requests-per-minute` caps the AV rate below 30 so operators can run the daily and minute daemons concurrently with headroom. (slice 125)
- **`mt data minute status [--verbose] [--json]`** — reports minute daemon health (alive/dead, last heartbeat, cycle count, current symbol) and per-symbol freshness (total/fresh/stale/failed/unfillable counts, work-queue size, stalest symbols, failed symbols). Supports `--verbose` (per-symbol table with watermark column) and `--json`. (slice 125)
- **`InstrumentRegistrySymbolSource`** (internal) — adapter that wraps `InstrumentRegistry.list_instruments(active_only=True)` to implement the `SymbolSource` protocol. Ensures preferred shares and test artifacts absent from the instrument registry do not appear in the minute work queue and burn AV API quota. (slice 125)
- **`MINUTE_DAEMON_ID`** constant — added to `daemon/types.py` alongside `DAILY_DAEMON_ID`. Both daemons coexist in the shared `daemon_heartbeat` table via distinct primary keys. (slice 125)
- **`mt data minute update SYMBOL [--months N]`** — fetch historical minute OHLCV data for a single symbol with per-month checkpointing. A crash after month 12 of 24 resumes at month 13 on the next invocation (uses the `acquisition_state` table with `granularity=minute`). `--months N` caps the fetch to the N most recent months for quick refreshes. (slice 124)
- **`mt data minute update-all [--months N] [--skip-recent/--no-skip-recent]`** — batch minute acquisition across all tracked symbols. Fail-fast semantics: the first failing symbol stops the batch so state rows accurately reflect a partial run; the next invocation skips freshly-OK symbols and retries failures. (slice 124)
- **`MinuteAcquisitionOrchestrator`** (internal) — composes the fixed `AlphaVantageMinuteProvider` + `TimescaleMinuteWriter` + slice 121's `run_acquisition_unit` with a per-month `ChunkProvider` adapter. Yields one checkpoint per calendar month. Foundation for the minute acquisition daemon (slice 125). (slice 124)
- **`mt data daily daemon`** — long-running foreground daemon that continuously cycles through the equity symbol universe, keeping daily OHLCV data current. Supports graceful SIGTERM/SIGINT shutdown (finishes current symbol, writes `STOPPED` heartbeat, exits 0), exponential backoff for failed symbols (`min(2^retry_count, 60)` minutes, excluded after `max_retries`), and interruptible sleep when caught up. (slice 123)
- **`mt data daily status`** — reports daemon health (alive/dead, last heartbeat, cycle count, current symbol) and per-symbol freshness (total/fresh/stale/failed counts, stalest symbols, failed symbols with error context). Supports `--verbose` (per-symbol table) and `--json`. (slice 123)
- **Daemon heartbeat table** — `daemon_heartbeat` migration (780) tracks daemon liveness without IPC; the CLI reads this row to determine alive/dead status. (slice 123)

### Removed
- **`marketservice.py`** — dead code removed. `daily_symbols` now calls `AlphavantageAPI.getSymbolListing()` directly; `newsagent.py` calls `MarketDB.readDailyOHLCVAdjusted()` directly; `bt.py` calls `MarketDB` directly. (slice 123)

- **Resumable daily acquisition** — `mt data daily update`, `update-all`, and `update-file` now resume correctly after a failure. If a run is interrupted, the next invocation skips symbols that already succeeded and retries only those that failed or were never reached. (slice 122)
- **`mt data state`** — new command to inspect acquisition state for all tracked symbols. Supports `--symbol`, `--granularity`, `--provider`, `--status`, and `--json` filters. Prints a clear message when no state has been recorded yet. (slice 121)
- **Acquisition state tracking** — the system now records per-symbol acquisition progress (last successful fetch, last attempt, error context, retry count) to a `acquisition_state` table on the TimescaleDB host. This is the foundation for resumable data acquisition in Initiative 120. (slice 121)
- **Tick event schema** — `tick_events` table ready to receive raw trade and quote ticks on a dedicated TimescaleDB instance (`MT_TICK_DB_URL`). Application-level ingestion comes in a later slice. (slice 105)
- **`mt data calendars list`** — list all registered trading calendars. (slice 104)
- **`mt data calendars holidays --calendar NYSE [--year 2026]`** — show holidays for a trading calendar with market status (full close / early close / late open). (slice 104)
- **`mt data instruments list`** — list instruments in the registry with optional `--venue`, `--asset-class`, `--inactive` filters. (slice 103)
- **`mt data instruments seed`** — populate the instrument registry from the MarketDB symbol list (`--dry-run` to preview). (slice 103)
- **`mt data migrate`** — apply pending schema migrations to TimescaleDB. (slice 102)
- **`mt data minute coverage [--symbol X]`** — show minute OHLCV data coverage fleet-wide or for a single symbol, including gap detection. (slice 101)
- **`mt data minute metrics`** — show TimescaleDB health: chunk count, size, compression ratio. (slice 101)
- **`mt data daily coverage`** — show daily OHLCV data coverage summary across all tracked symbols. (slice 101)
- **`mt data daily update/update-all/update-file/symbols/migrate`** — daily data acquisition commands. (slice 903)
- **`mt provider list/status/test`** — inspect configured providers and test connectivity. (slice 902)
- **`mt status`** — system health check: provider auth validity, DB connectivity. (slice 902)
- **`mt config list/get/set/path`** — manage persistent configuration (project/user/default precedence). (slice 900)

### Fixed
- **`AlphaVantageMinuteProvider._fetch_month`** — now sends the `month=YYYY-MM` and `extended_hours=true` query parameters to the API. Previously these were computed but never included in the request, so every call returned the same most-recent trailing data regardless of which month was requested. This is what unlocks true month-based pagination for minute data. (slice 124)
- **`RateLimiter.__aenter__`** — releases its internal lock before `asyncio.sleep`, so a second coroutine entering the rate limiter can independently check capacity and wait on the rate limit rather than blocking on the lock for the full sleep duration of the first. Required for concurrent coroutine fan-out (slice 125's minute daemon). (slice 124)
- **`InstrumentRegistry.get_instrument(symbol)`** — added; aliases `get_by_symbol`. `DataProcessor.classify_sessions` relied on this method but it had never been implemented, causing any caller of `DataProcessor.process()` to crash. (slice 124)
- **`AlphaVantageMinuteProvider.convert_to_standard_format`** — timestamps are now localized from US/Eastern (AlphaVantage's returned timezone) to UTC. Previously returned tz-naive timestamps that crashed downstream session classification. (slice 124)
- **`DataProcessor.classify_sessions`** — now skips gracefully when `_calendar` or `_registry` is None (logs at DEBUG, returns DataFrame unchanged). The `session_type` column is not persisted by `write_minute_data_bulk` anyway, so skipping is lossless; classification can be derived later when a `TradingCalendar` is wired. (slice 124)
- `mt data daily update-all` now correctly reads the least-recently-updated symbol list from the database. A pre-existing query bug caused it to fail silently when first exercised by this slice. (slice 122)
- `mt data minute coverage` previously crashed due to a broken async implementation; replaced with a working sync analyzer. (slice 101)
- `mt data daily update` error output was missing the `json_mode` parameter, causing a crash when `MT_MARKET_DB_URL` was unset. (slice 101)

### Removed
- `psycopg2`, `sqlalchemy`, `loguru`, `aiohttp` dependencies — replaced by psycopg3, psycopg_pool, stdlib logging, and httpx respectively. (slices 100, 901, 903)
- Deprecated CLI entry points (`market/ohlc.py`, `market/ohlcoptions.py`, `news/newsoptions.py`) and ~2,600 lines of deprecated minute data code. (slice 903)
