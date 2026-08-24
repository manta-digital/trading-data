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

- Continuation of `262-tasks.catalog-sync-with-settlement-capture-1.md`
  (Sections 1–5: constants, events, repository, test doubles, sync core).
  Read that file's Context Summary first; the rules, test commands, and
  branch stated there apply here unchanged.
- This file: Sections 6–10 — the `kalshi_004` migration, status reads, the
  CLI module with exit codes and the storage-failure proofs, recorder
  targets and fixtures, final validation and the throwaway-database
  rehearsal.
- Source of truth remains the slice design at
  `user/slices/262-slice.catalog-sync-with-settlement-capture.md`.

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
  - [ ] `open_sync_connection(settings) -> AsyncConnection` in a new
        `data/kalshi/db.py` (keeps the CLI thin and `repository.py` within
        the line guidance):
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
  - [ ] **Commit**: `feat: add mt data kalshi sync command with exit codes`.

- [ ] **Task 8.4: End-to-end sync scenarios on a throwaway database** (effort: 3)
  - [ ] In `test_kalshi_sync.py`, fake source + real repository, one test
        per criterion: first run populates the three tables with FKs
        satisfied and sets both `sync_state` columns (Success Criterion 1);
        identical second run writes zero rows and keeps `first_seen_at`
        (Criterion 2); close→awaiting, finalized→retired, removed-from-walk
        →looked-up (Criterion 3); aborting fake source mid-drain then
        re-run → no duplicates, no gap (Criterion 4); `--events-file`
        yields valid JSONL with `run_started` + 5 `phase_finished` +
        `run_finished` (Criterion 5).
  - [ ] Provider abort through the **real** client stack (review F003,
        Criterion 7's second clause): construct `KalshiClient(base_url=
        "http://127.0.0.1:1", max_retries=0)` (a closed local port —
        immediate connect refusal, no hang) with the real repository; the
        run exits `EXIT_PROVIDER`, the `run_finished` event carries the
        error, and `sync_state` is unchanged. This is the only test that
        exercises httpx → 261's transient mapping → exit 2 end to end.
  - [ ] **Commit**: `test: add kalshi sync end-to-end scenarios`.

- [ ] **Task 8.5: Storage failure proofs on a throwaway database** (effort: 3)
  - [ ] `test_kalshi_sync.py`: (a) a fake-source page carrying one market
        with an out-of-vocabulary status → row-by-row rewrite, that ticker
        as the only item error, every other row present, exit
        `EXIT_SYNC_PARTIAL`; (b) from a second connection,
        `pg_terminate_backend` the run's backend mid-walk (hook the fake
        source's page iterator to fire it after page 1) → exit
        `EXIT_STORAGE`, page 1 rows committed, `sync_state` unchanged;
        (c) covered by 8.2's lock test — reference it (Success Criterion 11).
  - [ ] **Commit**: `test: prove kalshi sync storage failure taxonomy`.

- [ ] **Task 8.6: `mt data kalshi status`** (effort: 2)
  - [ ] `--json` option. Opens a sync psycopg connection with the
        application credential, calls `read_catalog_status`; `None` prints
        "Kalshi catalog has never synced." and exits 0; otherwise prints the
        sections in the design's *CLI specification* (relative age for last
        full sync, markets by status, awaiting histogram, past-threshold
        count with oldest ticker and age in days, checked-directly count).
        No API call.
  - [ ] Unit test: never-synced path exits 0 with the message; `--json`
        emits a flat object with the documented keys (status read
        monkeypatched). Integration: after the 8.4 first-run test, `status`
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
        proven (test name or walkthrough step); Criterion 7's
        unreachable-base-URL clause is Task 8.4's real-client test.

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

## Task review disposition (20260824)

Review: `user/reviews/262-review.tasks.catalog-sync-with-settlement-capture.md`
(z-ai/glm-5.3, CONCERNS against `db876c4`; gate passed, re-review optional).
F003 → Task 8.4 real-client unreachable-URL test and Task 10.1 criteria walk.
F004 → Task 5.6 defines "completed window" as fully walked, clamped final
window advances the watermark, with a sub-window drain test. F005 → Task 3.6
tests `refresh_awaiting_close_times`. F006 → Task 5.8 returns `SyncOutcome`,
integers only in the CLI. F007 → Task 8.3 split into 8.3/8.4/8.5, interim
commit after Task 5.4, source-file split guidance in Section 5's intro,
`open_sync_connection` placed in `data/kalshi/db.py`. F008 → `mve_filter`
asserted on window (5.6) and lookup (5.7) queries and on every recorded
markets query after a full run (5.8). F001/F002/F009 pass — no action.
