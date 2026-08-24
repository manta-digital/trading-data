---
docType: tasks
slice: catalog-sync-with-settlement-capture
project: trading-data
lld: user/slices/262-slice.catalog-sync-with-settlement-capture.md
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [261]
interfaces: [263, 264, 265, 266]
projectState: >
  Slice 261 (Kalshi provider foundation) is merged on main: async
  KalshiClient (public + authenticated modes), Pydantic models, the kalshi
  migration track (kalshi_001..003), recorded fixtures, and the recorder
  script. The kalshi track is applied to production (2026-08-24, 0 pending).
  Slice design 262 is reviewed (CONCERNS, gate passed) with the storage
  failure taxonomy folded in at 1ddc25b. No sync, repository, status, or
  CLI code exists for kalshi yet.
dateCreated: 20260824
dateUpdated: 20260824
status: not_started
---

## Context Summary

- Working on **262 Catalog Sync with Settlement Capture** — the first
  collection logic of initiative 260: `mt data kalshi sync` (full walk of the
  non-MVE live catalog every run, windowed settled stream, awaiting-settlement
  guarantee) and the first `mt data kalshi status`.
- Source of truth: the slice design at
  `user/slices/262-slice.catalog-sync-with-settlement-capture.md`. Its
  **Technical Decisions 1–14**, **Storage failure taxonomy**, **Repository
  contract**, and **CLI specification** are referenced below rather than
  restated. Read the design before starting any section.
- Key patterns to reuse (do not reinvent): `data/kalshi/client.py`
  (`KalshiClient`, `MarketsQuery`/`EventsQuery` TypedDicts),
  `data/acquisition/events.py` (event type / dataclass / sink shape — mirror,
  do not import), `market/db_session.make_configure_connection` and
  `constants.DB_BULK_SESSION` (session settings), `market/schema/runner.py`
  and `migrations/kalshi.py` (track), `test/conftest.py::ephemeral_db`
  (throwaway DB), `test/integration/test_kalshi_migrations.py` (kalshi
  integration-test shape), `scripts/record_kalshi_fixtures.py` (recorder).
- Hard rules from the design: every markets request carries
  `mve_filter=exclude`; no status literal in SQL (pass `MarketStatus` values
  as parameters); one `psycopg.AsyncConnection` per run, no pool; one
  transaction per written page; nothing references `public`; the storage
  path catches only the enumerated `psycopg.Error` rows — everything else
  propagates.
- Tests: unit tier `uv run pytest test/unit/data/kalshi -q`; integration
  tier only through `uv run python scripts/run_tests.py integration -- -k kalshi -q`
  (never with the production URL). Type gate: `uv run --extra dev mypy
  src/manta_trading/data/kalshi src/manta_trading/cli/commands/kalshi.py` and
  `npx --yes pyright` strict on the same paths plus tests.
- Deployment prerequisite already met: kalshi_001–003 are on production.
  Applying this slice's `kalshi_004` to production is a PM action
  (walkthrough step 0), not a task here.
- Next slice: 263 (supervised pass unit) consumes `CatalogSync.run()`, the
  exit-code constants, and the event sink.
- Branch per CLAUDE.md git rules: `262-slice.catalog-sync-with-settlement-capture`
  from `main` (no integration branch configured). Commit checkpoints are
  marked; semantic prefixes.

## Section 1: Constants and configuration

- [ ] **Task 1.1: Add sync constants to `data/kalshi/constants.py`** (effort: 1)
  - [ ] Add, with one-line comments citing the design decision each serves:
        `CATALOG_WALK_FILTERS` (tuple of `MarketStatusFilter` UNOPENED, OPEN,
        PAUSED, CLOSED — Decision 1), `KALSHI_MVE_FILTER = "exclude"`
        (Decision 2), `MARKETS_PAGE_LIMIT = 1000`, `EVENTS_PAGE_LIMIT = 200`,
        `TICKERS_BATCH_SIZE = 100` (Decision 9), `SETTLED_WINDOW`
        (6 h) and `WINDOW_OVERLAP` (1 s) (Decision 4),
        `KALSHI_SETTLEMENT_STUCK_AFTER` (7 d, Decision 10),
        `AWAITING_AGE_BUCKETS` (1 d, 7 d, 30 d — the 7 d edge references
        `KALSHI_SETTLEMENT_STUCK_AFTER`, not a second literal),
        `DB_CONNECT_TIMEOUT_SECONDS`, `SYNC_ADVISORY_LOCK_KEY` (fixed bigint).
  - [ ] Success: `test/unit/data/kalshi/test_constants.py` gains assertions
        that every walk filter is a `MarketStatusFilter`, `SETTLED` is not
        among them, `AWAITING_AGE_BUCKETS` contains
        `KALSHI_SETTLEMENT_STUCK_AFTER`, and buckets are strictly increasing.

- [ ] **Task 1.2: `Settings.kalshi_requests_per_minute`** (effort: 1)
  - [ ] Add `kalshi_requests_per_minute: int | None = None` to `Settings`
        (`config/__init__.py`) beside the kalshi credential fields, with a
        comment: overrides the mode's rate budget at client construction
        (Decision 13); `None` keeps 261's mode constants.
  - [ ] Success: unit test in the existing config test module shows
        `MT_KALSHI_REQUESTS_PER_MINUTE=120` loads as `120` and unset is `None`.
  - [ ] **Commit**: `feat: add kalshi sync constants and rate-budget setting`.

## Section 2: Structured events (`data/kalshi/events.py`)

- [ ] **Task 2.1: Event types, dataclass, and sinks** (effort: 2)
  - [ ] Create `events.py` mirroring the shape of
        `data/acquisition/events.py` but Kalshi-typed (Decision 14):
        `SyncEventType` StrEnum {`run_started`, `phase_finished`,
        `item_error`, `run_finished`}; frozen `SyncEvent` dataclass
        (`run_id`, `timestamp`, `event_type`, `phase`, `counts: dict[str,int]`,
        `transitions: dict[str,int]`, `ticker: str | None`,
        `error: str | None`, `duration_ms: int | None`) with a `to_dict()`
        that is JSON-serializable; `SyncEventSink` Protocol with `emit`;
        `NullSyncEventSink`; `JsonlSyncEventSink(path)` appending one JSON
        object per line.
  - [ ] Sink failures are best-effort per the taxonomy: the *caller* wraps
        `emit` (Task 5.7); the sinks themselves do not swallow.
  - [ ] Success: module imports; no field named `symbol`/`granularity`; no
        import from `data/acquisition`.

- [ ] **Task 2.2: Unit tests for events** (effort: 1)
  - [ ] `test/unit/data/kalshi/test_events.py`: `to_dict()` round-trips
        through `json.dumps`; `JsonlSyncEventSink` writes N valid lines for N
        events (tmp_path); `NullSyncEventSink.emit` is a no-op; every
        `SyncEventType` value is a valid identifier string.
  - [ ] **Commit**: `feat: add kalshi sync structured events and sinks`.

## Section 3: Repository (`data/kalshi/repository.py`) — all SQL

Every statement for `kalshi.*` lives here (design: Repository contract). The
class takes an open `psycopg.AsyncConnection`; it never opens one. Each
public method runs in the caller's transaction (`async with conn.transaction()`
around a page is the sync core's job — Task 5.2), so tests can drive
granularity explicitly. Enum values are parameters (`MarketStatus.FINALIZED`
etc.); status literals must not appear in SQL text.

- [ ] **Task 3.1: Integration test scaffolding for the repository** (effort: 2)
  - [ ] Create `test/integration/test_kalshi_repository.py` with a fixture
        that applies `TRACKS["kalshi"]` to `ephemeral_db` (the
        `test_kalshi_migrations.py` pattern) and yields an
        `AsyncConnection` (plain `async def` tests — `pyproject.toml` sets
        `asyncio_mode = "auto"`) wrapped in a `CatalogRepository`.
  - [ ] Add row-builder helpers that load `test/fixtures/kalshi/series_list.json`,
        `events_page1.json`, `markets_page1.json` into the 261 Pydantic
        models so tests write real served shapes, not hand-typed dicts.
  - [ ] Success: the fixture yields a repository against an empty migrated
        `kalshi` schema; the file is discovered by
        `run_tests.py integration -- -k kalshi`.

- [ ] **Task 3.2: `upsert_series` with the write-on-change guard** (effort: 2)
  - [ ] One parameterized multi-row `INSERT … ON CONFLICT (ticker) DO UPDATE
        SET …, last_synced_at = now() WHERE kalshi.series.raw IS DISTINCT
        FROM EXCLUDED.raw` (Decision 6). Column mapping one-to-one from the
        `Series` model; `raw = model_dump(mode="json")`. Returns the written
        row count (`rowcount`). Empty input returns 0 without a round trip.
  - [ ] Test: first upsert writes N; identical second upsert writes 0 and
        leaves `first_seen_at` unchanged; a changed `title` writes 1 and
        bumps `last_synced_at` only for that row.

- [ ] **Task 3.3: `upsert_events`** (effort: 1)
  - [ ] Same shape as 3.2 for `kalshi.events` (`Event` model, PK
        `event_ticker`, FK `series_ticker`).
  - [ ] Test: write-on-change proven as in 3.2; upserting an event whose
        series is absent raises `psycopg.errors.ForeignKeyViolation`
        (nothing in the repository catches it — that is Task 5.2's job).

- [ ] **Task 3.4: `upsert_markets` returning `MarketUpsertOutcome`** (effort: 3)
  - [ ] Within the caller's transaction: select prior `(ticker, status)` for
        the page's tickers, run the multi-row upsert (`Market` model, PK
        `ticker`), and return a frozen `MarketUpsertOutcome(written: int,
        transitions: dict[tuple[str, str], int])` counting `from→to` only
        where the status actually changed (Decision 7). New rows are not
        transitions.
  - [ ] Test: page of new markets → `written == N`, `transitions == {}`;
        re-upsert with one market moved `active→closed` in the fixture copy →
        `written == 1`, `transitions == {("active","closed"): 1}`; unchanged
        re-upsert → 0 / `{}`; a status outside `MarketStatus` raises
        `psycopg.errors.CheckViolation`.

- [ ] **Task 3.5: Parent lookups** (effort: 1)
  - [ ] `known_event_tickers(tickers) -> set[str]` and
        `known_series_tickers(tickers) -> set[str]` (single `= ANY(%s)`
        query each; empty input → empty set, no round trip).
  - [ ] Test: returns exactly the subset present after 3.2/3.3 writes.

- [ ] **Task 3.6: Awaiting-settlement statements** (effort: 2)
  - [ ] `enter_awaiting(now) -> int` (INSERT … SELECT from markets where
        `close_time <= now` and `status <> FINALIZED`, `ON CONFLICT DO
        NOTHING`), `retire_awaiting() -> int` (DELETE USING markets where
        `status = FINALIZED`), `refresh_awaiting_close_times() -> int`
        (UPDATE where the stored market's `close_time` differs),
        `awaiting_tickers() -> list[str]`, `mark_checked(tickers, now)`
        (sets `last_checked_at`).
  - [ ] Test: a market with past `close_time` and status `active` enters;
        one with future `close_time` does not; upserting the entered market
        as `finalized` with `result` then `retire_awaiting()` removes it;
        `mark_checked` sets `last_checked_at` for the given tickers only.

- [ ] **Task 3.7: `sync_state` accessors** (effort: 1)
  - [ ] `get_sync_state(surface) -> SyncState | None` (frozen dataclass:
        `last_full_sync_at`, `watermark_ts`, `cursor`),
        `set_last_full_sync(surface, ts)`, `set_watermark(surface, ts)`
        — each an upsert on `surface` that touches only its column plus
        `updated_at`; `cursor` is never written by 262.
  - [ ] Test: `None` on an empty table; set watermark → last_full_sync stays
        NULL; set both → both read back; `cursor` stays NULL.
  - [ ] **Commit**: `feat: add kalshi catalog repository with integration tests`.

## Section 4: Test doubles for the sync core

Test infrastructure before the core (Phase 5 guide: test-with).

- [ ] **Task 4.1: `CatalogSource` Protocol and a fixture-backed fake** (effort: 2)
  - [ ] Define `CatalogSource` in `sync.py` (the six calls listed in the
        design's *CatalogSource protocol*); `KalshiClient` must satisfy it
        structurally — assert with a typed assignment in a unit test.
  - [ ] Create `test/unit/data/kalshi/_fake_source.py`: `FakeCatalogSource`
        serving 261 fixtures (`series_list`, `markets_page*`, `events_page*`,
        `historical_cutoff`) plus in-memory overrides per test; records every
        received query (`MarketsQuery`/`EventsQuery` dicts) so tests can
        assert `mve_filter`, `status`, `min_settled_ts`/`max_settled_ts`,
        `tickers` batches; supports `raise_on(call, exc)` to inject
        `ProviderTransientError`/`ProviderPermanentError` at a chosen call.
  - [ ] Windowed settled responses: the fake filters an in-memory settled
        list by `min_settled_ts`/`max_settled_ts` (strict after/before, second
        granularity) and serves newest-first, matching the survey behavior.
  - [ ] Success: fake type-checks against `CatalogSource`; a smoke test
        iterates it for each walk filter and gets the fixture markets.

- [ ] **Task 4.2: In-memory fake repository** (effort: 2)
  - [ ] `test/unit/data/kalshi/_fake_repository.py`: same public surface as
        `CatalogRepository` (Tasks 3.2–3.7) over dicts; implements
        write-on-change by comparing `raw`; computes transitions; keeps an
        `awaiting` dict and a `sync_state` dict; exposes `writes` log
        (method, count) for ordering assertions; `fail_on(method, exc)` to
        inject `psycopg.OperationalError` / `IntegrityError` subclasses
        (instantiate `psycopg.errors.CheckViolation` etc. directly).
  - [ ] Provide a `transaction()` async context manager that records begin /
        commit / rollback so the core's per-page granularity is testable.
  - [ ] Success: imports; a smoke test upserts a fixture page and reads it
        back; **Commit**: `test: add fake source and repository for kalshi sync`.

## Section 5: Sync core (`data/kalshi/sync.py`)

`CatalogSync(source, repository, sink, clock)` with `async run(settled_since)
-> SyncResult`. No httpx, no typer, no SQL. Each phase is its own small
method emitting one `phase_finished` event. Unit tests live in
`test/unit/data/kalshi/test_sync.py` (split into `test_sync_walk.py` /
`test_sync_settled.py` if either file passes ~300 lines).

- [ ] **Task 5.1: `SyncResult` and run skeleton** (effort: 2)
  - [ ] `SyncResult` dataclass: per-phase counts (fetched / written /
        unchanged), `transitions`, `settled_captured`, `windows_completed`,
        awaiting `entered/retired/checked/unreachable`, `item_errors:
        list[ItemError(ticker, phase, reason)]`, `duration_ms`, `error:
        str | None`, and `to_dict()` (JSON-serializable from day one —
        design Special Considerations).
  - [ ] `run()` emits `run_started`, executes phases 1–6 in the design's
        Data Flow order, emits `run_finished` (with `error` set when a
        provider or storage exception is propagating), returns the result.
        `clock` is injectable (`Callable[[], datetime]`) and the run start
        time is captured once.
  - [ ] Test: with an empty fake source, `run()` emits exactly
        `run_started`, five `phase_finished` (phases named `series`,
        `markets`, `events`, `settled`, `awaiting`), `run_finished`, in order.

- [ ] **Task 5.2: Per-page transaction wrapper and integrity fallback** (effort: 3)
  - [ ] `_write_page(...)` helper: opens `repository.transaction()`, writes
        parent events then markets, commits. On `psycopg.IntegrityError`:
        roll back, then re-write the page **row by row**, each in its own
        transaction; rows that raise `IntegrityError` again become
        `ItemError` (ticker, SQLSTATE at ERROR via `logger.error`) and
        `item_error` events; the run continues (taxonomy row 4/5).
        `psycopg.OperationalError` and any other `psycopg.Error` are **not**
        caught here.
  - [ ] Test (fake repo): inject `CheckViolation` on the page write for one
        ticker → every other row written, exactly one `item_error`, result
        marked partial; inject `OperationalError` → propagates out of
        `run()` after a `run_finished` event with `error`; inject
        `ProgrammingError` → propagates with no `run_finished` handling of
        its own beyond the generic re-raise path (assert it is not converted).

- [ ] **Task 5.3: Phase 1 — series** (effort: 1)
  - [ ] `_sync_series()`: one `get_series_list()`, one `upsert_series` page
        (in a transaction), keep the set of series tickers for parent
        resolution, emit `phase_finished` with fetched/written.
  - [ ] Test: counts match the fixture; phase event carries them.

- [ ] **Task 5.4: Phase 2 — markets walk with parent resolution** (effort: 4)
  - [ ] `_walk_markets()`: for each filter in `CATALOG_WALK_FILTERS`, call
        `iter_markets(status=…, mve_filter=KALSHI_MVE_FILTER,
        limit=MARKETS_PAGE_LIMIT)` and buffer into pages of
        `MARKETS_PAGE_LIMIT`; per page: collect unknown `event_ticker`s
        (`known_event_tickers`), fetch them with `get_events(tickers=…)` in
        `TICKERS_BATCH_SIZE` batches, fetch any unknown `series_ticker` via
        `get_series`; a market whose parent cannot be obtained is skipped
        with an `ItemError` (Decision 9 — never a placeholder row); write
        the page via 5.2; accumulate transitions; add tickers to `seen`.
  - [ ] Test: every markets query received by the fake carries
        `mve_filter == "exclude"` and a filter from `CATALOG_WALK_FILTERS`
        (Success Criterion 9); unknown event resolved by a `tickers` batch
        (assert batch size ≤ 100 across a synthetic 250-ticker page);
        unknown series fetched once; parent silently omitted by the API →
        dependent markets skipped, `item_errors` lists them, run continues;
        transitions aggregate across pages; `seen` holds every walked ticker.

- [ ] **Task 5.5: Phase 3 — events refresh** (effort: 1)
  - [ ] `_refresh_events()`: when `last_full_sync_at` is set, page through
        `get_events(min_updated_ts = last_full_sync_at − WINDOW_OVERLAP,
        limit=EVENTS_PAGE_LIMIT)` following the cursor and upsert; skip
        entirely on a first run (no floor). Events whose series is unknown
        resolve as in 5.4.
  - [ ] Test: first run issues no `min_updated_ts` request; second run
        issues one with the floor `last_full_sync_at − 1 s`; cursor pages
        are followed until empty.

- [ ] **Task 5.6: Phase 4 — settled stream in windows** (effort: 4)
  - [ ] `_drain_settled(settled_since)`: floor = `settled_since` if given,
        else `watermark_ts`, else `get_historical_cutoff().market_settled_ts`
        (Decision 5). Walk windows `[a, b)` of `SETTLED_WINDOW` oldest-first
        up to the run start time, requesting
        `min_settled_ts = a − WINDOW_OVERLAP`, `max_settled_ts = b`,
        `mve_filter = exclude`, following the cursor; upsert each page via
        5.2 (parents resolved as in 5.4); after each **complete** window
        `set_watermark(surface, b)` in its own transaction; add captured
        tickers to `captured`. The final partial window ends at the run
        start time.
  - [ ] Test: floor selection for the three cases; window boundaries and
        the 1 s overlap asserted on received queries; watermark advances
        once per completed window; a `ProviderTransientError` injected in
        window 3 leaves the watermark at the end of window 2 and propagates;
        a re-run from that watermark re-walks only from window 3 (no gap);
        overlap duplicates cost zero writes (fake repo write-on-change);
        `--settled-since` later than the watermark is honored for that run
        and does not move the watermark backwards on completion.

- [ ] **Task 5.7: Phase 5 — awaiting maintenance and vanished reconciliation** (effort: 3)
  - [ ] `_reconcile_awaiting()`: in one transaction run
        `refresh_awaiting_close_times`, `enter_awaiting(now)`,
        `retire_awaiting`; then `vanished = awaiting_tickers() − seen −
        captured`; look vanished up with `get_markets(tickers=…)` in
        `TICKERS_BATCH_SIZE` batches, upsert what returns (transitions
        counted), `retire_awaiting` again, `mark_checked(vanished, now)`;
        tickers the API omitted are counted `unreachable` (not errors — the
        market stays in the set). Sink `emit` failures across the whole core
        are wrapped once here or in 5.1: `logger.error`, never abort.
  - [ ] Test: a market walked as `active` with `close_time` in the past
        enters; a settled-stream capture of it retires it; an awaiting
        ticker absent from both walk and stream is looked up by ticker and
        `last_checked_at` set; an omitted ticker is `unreachable`; `emit`
        raising does not fail the run.

- [ ] **Task 5.8: Phase 6 — state, and exit classification** (effort: 2)
  - [ ] After phase 5, `set_last_full_sync(surface, run_start)` in its own
        transaction. Add a pure `classify(result, exc) -> int` function (or
        `SyncResult.exit_code` property fed by the CLI's constants — pick
        one, used by both CLI and 263): 0 clean, 3 when `item_errors` is
        non-empty, 2 on provider exception, 4 on `OperationalError`
        (Decision 11). The exit-code constants live in
        `cli/commands/kalshi.py` (Task 8.1) — the core returns a
        classification enum, not integers, to avoid a core→CLI import.
  - [ ] Test: `last_full_sync_at` is written only after phase 5 completes
        and is **not** written when a provider error aborts phase 2 (state
        advanced only as far as completed); classification table covered
        for all four outcomes.
  - [ ] Run the type gate on `data/kalshi` and tests; **Commit**:
        `feat: add kalshi catalog sync core with unit tests`.

## Section 6: Migration `kalshi_004_catalog_sync_semantics`

- [ ] **Task 6.1: Append the migration** (effort: 1)
  - [ ] Add entry `kalshi_004_catalog_sync_semantics` to `KALSHI_MIGRATIONS`:
        `COMMENT ON COLUMN` for `kalshi.sync_state.last_full_sync_at`,
        `.watermark_ts`, `.cursor` with the exact wording of the design's
        *State Management* table. Comments only; idempotent.
  - [ ] Test (`test_kalshi_migrations.py`): `kalshi_004` is in
        `TRACKS["kalshi"]`; applies and re-applies on `ephemeral_db`; the
        `watermark_ts` comment read via `col_description` contains
        "completed settled window"; existing 261 tests still pass (Success
        Criterion 8).
  - [ ] **Commit**: `feat: add kalshi_004 sync_state comment migration`.

## Section 7: Status reads (`data/kalshi/status.py`)

- [ ] **Task 7.1: Status queries and dataclass** (effort: 2)
  - [ ] Synchronous psycopg (one short read, the `mt data status` pattern).
        `read_catalog_status(conn) -> CatalogStatus | None`: `None` when no
        `sync_state['catalog']` row; otherwise `last_full_sync_at`,
        `watermark_ts`, series/events counts, markets by status (dict keyed
        by `MarketStatus`), awaiting total, age histogram over
        `AWAITING_AGE_BUCKETS` (`now() − close_time`), count past
        `KALSHI_SETTLEMENT_STUCK_AFTER`, oldest awaiting (ticker, age),
        count with `last_checked_at` set. Bucket edges and threshold are
        SQL parameters from the constants. `to_dict()` for `--json`.
  - [ ] Test (`test_kalshi_repository.py` or a sibling integration file):
        empty schema → `None`; after seeding via the repository (Section 3)
        with markets at ages 0.5 d, 3 d, 10 d, 40 d → histogram
        `{<1d:1, 1–7d:1, 7–30d:1, >30d:1}`, past-threshold 2, oldest is the
        40 d ticker (Success Criterion 6).
  - [ ] **Commit**: `feat: add kalshi catalog status reads`.

## Section 8: CLI module (`cli/commands/kalshi.py`)

- [ ] **Task 8.1: Exit-code constants and module skeleton** (effort: 1)
  - [ ] Create `kalshi_app = typer.Typer(...)`; define `EXIT_OK = 0`,
        `EXIT_PREFLIGHT = 1`, `EXIT_PROVIDER = 2`, `EXIT_SYNC_PARTIAL = 3`,
        `EXIT_STORAGE = 4` with a mapping from the core's classification
        enum (Task 5.8). Attach in `data.py`:
        `data_app.add_typer(kalshi_app, name="kalshi")` beside the existing
        `add_typer` lines.
  - [ ] Success: `uv run mt data kalshi --help` lists `sync` and `status`.

- [ ] **Task 8.2: Async preflight — connection, session, track, lock** (effort: 3)
  - [ ] `open_sync_connection(settings) -> AsyncConnection` (in
        `data/kalshi/repository.py` or a small `db.py` — keep the CLI thin):
        `AsyncConnection.connect(url, connect_timeout=DB_CONNECT_TIMEOUT_SECONDS)`
        using the application credential (`MT_TIMESCALE_DB_URL`, never the
        maintenance URL); apply the `SET`s from `DB_BULK_SESSION` (same
        values `make_configure_connection` issues — reuse its statement
        builder if one is exposed, otherwise extract one so the list is not
        duplicated); verify `kalshi.sync_state` exists (`to_regclass`) else
        raise a typed `PreflightError("apply the kalshi track")`; take
        `pg_try_advisory_lock(SYNC_ADVISORY_LOCK_KEY)` else
        `PreflightError("another sync holds the lock")`. Lock is released
        by closing the connection at the end of the run.
  - [ ] Integration test (`test/integration/test_kalshi_sync.py`, created
        here with the migrated-`ephemeral_db` fixture from Task 3.1):
        session settings visible via `SHOW statement_timeout`; unmigrated
        database → `PreflightError` naming the track; a second connection
        holding the lock → `PreflightError` naming the lock (Success
        Criterion 11, third case).

- [ ] **Task 8.3: `mt data kalshi sync`** (effort: 3)
  - [ ] Options `--settled-since ISO-8601` (parsed to aware UTC datetime;
        naive input rejected), `--events-file PATH`, `--json`. Body:
        settings → `KalshiClient.from_settings` with `rate_limit =
        RateLimit(requests_per_minute=settings.kalshi_requests_per_minute)`
        when set (log mode and budget at INFO as 261 does) → preflight
        (8.2; failures print the message and exit `EXIT_PREFLIGHT`) → sink
        (`JsonlSyncEventSink` or Null) → `asyncio.run(CatalogSync(...).run(...))`
        → print the `SyncResult` summary as a Rich table (per-phase
        fetched/written, transitions, settled windows/captured, watermark,
        awaiting counts, item errors count) or `--json` → exit via the 8.1
        mapping. `ProviderTransientError`/`ProviderPermanentError` →
        `EXIT_PROVIDER`; `psycopg.OperationalError` → `logger.exception`,
        `EXIT_STORAGE`. Client and connection closed in `finally`.
  - [ ] Unit test (`test/unit/cli/test_kalshi_commands.py`, typer
        `CliRunner`, core and connection monkeypatched): exit-code mapping
        for all five outcomes; `--settled-since` naive → error; `--json`
        output parses and contains the phase counts; budget override reaches
        the client constructor (Success Criterion 7).
  - [ ] Integration test (`test_kalshi_sync.py`, fake source + real
        repository): first run populates the three tables with FKs
        satisfied and sets both `sync_state` columns (Success Criterion 1);
        identical second run writes zero rows and keeps `first_seen_at`
        (Criterion 2); close→awaiting, finalized→retired, removed-from-walk
        →looked-up (Criterion 3); aborting fake source mid-drain then
        re-run → no duplicates, no gap (Criterion 4); `--events-file`
        yields valid JSONL with `run_started` + 5 `phase_finished` +
        `run_finished` (Criterion 5).

- [ ] **Task 8.4: Storage failure proofs on a throwaway database** (effort: 3)
  - [ ] `test_kalshi_sync.py`: (a) a fake-source page carrying one market
        with an out-of-vocabulary status → row-by-row rewrite, that ticker
        as the only item error, every other row present, exit
        `EXIT_SYNC_PARTIAL`; (b) from a second connection,
        `pg_terminate_backend` the run's backend mid-walk (hook the fake
        source's page iterator to fire it after page 1) → exit
        `EXIT_STORAGE`, page 1 rows committed, `sync_state` unchanged;
        (c) covered by 8.2's lock test — reference it (Success Criterion 11).
  - [ ] **Commit**: `feat: add mt data kalshi sync command with exit codes`.

- [ ] **Task 8.5: `mt data kalshi status`** (effort: 2)
  - [ ] `--json` option. Opens a sync psycopg connection with the
        application credential, calls `read_catalog_status`; `None` prints
        "Kalshi catalog has never synced." and exits 0; otherwise prints the
        sections in the design's *CLI specification* (relative age for last
        full sync, markets by status, awaiting histogram, past-threshold
        count with oldest ticker and age in days, checked-directly count).
        No API call.
  - [ ] Unit test: never-synced path exits 0 with the message; `--json`
        emits a flat object with the documented keys (status read
        monkeypatched). Integration: after the 8.3 first-run test, `status`
        via `CliRunner` against the throwaway URL shows non-zero counts.
  - [ ] **Commit**: `feat: add mt data kalshi status command`.

## Section 9: Recorder targets and fixtures

- [ ] **Task 9.1: Three new recorder targets** (effort: 2)
  - [ ] In `scripts/record_kalshi_fixtures.py` add `markets_by_tickers`
        (`GET /markets?tickers=` with ~5 tickers taken from
        `markets_page1.json` plus one bogus ticker, proving silent omission),
        `events_by_tickers` (`GET /events?tickers=` with ~5 event tickers
        from `events_page1.json`), `markets_settled_window`
        (`min_settled_ts`/`max_settled_ts` spanning one recent hour,
        `mve_filter=exclude`, `limit=50`). Register in `RECORDERS`; respect
        `--dry-run`.
  - [ ] Run each with `--only` (developer-run, live) and commit the three
        JSON files under `test/fixtures/kalshi/`.
  - [ ] Test (`test_fixtures.py`): each new fixture parses into the 261 page
        models; the by-tickers fixture has fewer markets than tickers
        requested (the bogus one omitted); every settled-window market is
        `finalized` with `settlement_ts` inside the requested window.

- [ ] **Task 9.2: Point the fakes at the recorded shapes** (effort: 1)
  - [ ] `FakeCatalogSource` (Task 4.1) serves `markets_by_tickers.json` /
        `events_by_tickers.json` for `tickers=` calls and
        `markets_settled_window.json` as its default settled population;
        re-run the Section 5 unit tests unchanged.
  - [ ] **Commit**: `test: record tickers-batch and settled-window kalshi fixtures`.

## Section 10: Final validation and walkthrough

- [ ] **Task 10.1: Full validation** (effort: 1)
  - [ ] `uv run pytest test/unit -q` green; `uv run python
        scripts/run_tests.py integration -- -k kalshi -q` green (re-run a
        known-flake in isolation before investigating — see memory);
        `ruff check`, mypy, strict pyright clean on the kalshi package, the
        CLI module, and tests (Success Criterion 10); no new direct
        dependency in `pyproject.toml`.
  - [ ] Walk the design's Success Criteria 1–11 and note where each is
        proven (test name or walkthrough step).

- [ ] **Task 10.2: Throwaway-database rehearsal (walkthrough steps 2–5)** (effort: 2)
  - [ ] Against a test-cluster throwaway database (runbook 400): apply the
        track, run `sync --settled-since <today T00:00Z> --events-file …`,
        verify exit 0 and the summary shape; run `sync` again and verify
        written 0/0/0 (modulo live changes); `status` shows every section;
        `wc -l` on the events file matches 7 lines per run.
  - [ ] Interrupted drain: run with `--settled-since` a few weeks back,
        Ctrl-C after ≥2 windows, confirm `status` shows the watermark at the
        last completed window end, re-run, confirm completion with no
        duplicate rows (`count(*)` vs `count(distinct ticker)` is trivially
        equal — compare per-window counts to a clean run instead).
  - [ ] Live settlement observation (walkthrough step 3): pick an awaiting
        15-minute crypto market close to settlement, re-run `sync` a few
        minutes later, confirm the row is `finalized` with `result` and gone
        from `awaiting_settlement`. Record the ticker and timestamps in the
        walkthrough.
  - [ ] Drop the throwaway database afterwards (it is one this session
        created).

- [ ] **Task 10.3: Refine the walkthrough and close out** (effort: 1)
  - [ ] Update the design's *Verification Walkthrough* to the actual
        commands and output observed in 10.2; note that steps 0 (apply
        `kalshi_004` to production) and 6 (first production run, ~15 min
        drain, from an interactive shell with `--events-file`) are PM /
        operator actions to run after merge — not performed by this slice's
        automation.
  - [ ] Delegate checklist updates to the task-checker agent; ensure all
        completed tasks are checked; set the task file's `status`.
  - [ ] **Commit**: `docs: refine 262 walkthrough post-implementation`.
