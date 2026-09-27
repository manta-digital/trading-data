---
docType: tasks
slice: databento-adapter-and-cost-preflight
project: trading-data
lld: user/slices/220-slice.databento-adapter-and-cost-preflight.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [902]
interfaces: [222, 223, 224, 226, 229, 230]
projectState: >
  Slice 220 design committed at f706c71 (review round 2 CONCERNS, passes the
  gate). No tick code exists yet: no `databento` dependency, no
  `data/tick/` package, no `mt data tick` group. Provider registry (902)
  already has `ProviderType.DATABENTO` and the `databento` profile;
  `Settings.databento_api_key` exists. The PM's Databento account does not
  exist yet; Sections 1–6 need no key, Section 7 does.
dateCreated: 20260927
dateUpdated: 20260927
status: complete
---

## Context Summary

- Working on **220 Databento adapter and cost preflight**, the first slice of
  the futures-tick initiative. It adds the `databento` SDK, the tick
  protocols, one adapter, a DBN file reader, and one CLI verb,
  `mt data tick estimate`, which calls only free metadata endpoints. It buys
  nothing and touches no database.
- Source of truth: the slice design. Tasks cite its sections by name
  ("Technical Decision 7, the byte budget"); read the cited section before
  each task. The design's findings table holds the verified SDK facts (record
  sizes, inclusive/exclusive end dates, SDK timeouts, error classes).
- Sections follow the design's Development Approach, one per step:
  (1) dependency, constants, error class, setting; (2) sample fixtures and the
  file reader; (3) metadata protocol and adapter; (4) acquisition methods and
  the verified download; (5) estimate core and CLI; (6) decode benchmark;
  (7) recorded fixtures, docs, and the live walkthrough.
- **Section 7 needs the PM's Databento account** (`MT_DATABENTO_API_KEY` in
  the dev `.env`, provider-side budget set). The slice does not close until
  Section 7 is done: recorded metadata fixtures committed and every
  metadata-shaped test running on them (Technical Decision 11).
- Code the tasks touch or copy from:
  - `providers/errors.py` — `ProviderError` and its three subclasses.
  - `config/__init__.py` — `Settings`; `databento_api_key` (~line 128).
  - `data/kalshi/constants.py` — pattern for a per-domain constants module.
  - `cli/commands/kalshi.py` — pattern for a source subgroup; exit codes
    defined once (`EXIT_OK`, `EXIT_PREFLIGHT`, `EXIT_PROVIDER`, ~line 57).
  - `cli/commands/data.py` — subgroup registration block (~lines 84–108).
  - `cli/output.py` — `print_result`, `print_error`, `make_table`.
  - `scripts/record_kalshi_fixtures.py` — pattern for the recording script.
  - `test/unit/test_settings.py` — `Settings(_env_file=None)` + `monkeypatch`.
  - `test/unit/cli/commands/test_data_kalshi.py` — `CliRunner` test pattern.
- Tests: `uv run --extra dev pytest test/unit/data/tick
  test/unit/cli/commands/test_data_tick.py -q`. Unit tier only; no network,
  no key. Run mypy over the touched `src` paths and tests in one invocation.
- Commit at least once per section. Scope `ruff format` to touched files and
  check `git diff main` for unintended deletions before each commit.
- Next slices: 222 (tick storage) and 223 (acquisition pass) consume this
  slice's protocols, enums, and error class.

## Section 1: Dependency, constants, error class, setting

Design: *Technical Scope*, *Technical Decisions 4, 5, 10* (schema enum,
spend ceiling, error mapping), *Settings*.

- [x] **Task 1.1: Add the `databento` dependency** (effort: 1)
  - [x] Add `databento>=0.87.0` to `pyproject.toml` dependencies; run
        `uv lock` and `uv sync`.
  - [x] Success: `uv run python -c "import databento; print(databento.__version__)"`
        prints ≥ 0.87.0; `pyproject.toml` and `uv.lock` are staged together.
- [x] **Task 1.2: `data/tick/constants.py`** (effort: 2)
  - [x] Create `src/manta_trading/data/tick/__init__.py` and `constants.py`.
  - [x] `TickSchema(StrEnum)`: `TRADES="trades"`, `TBBO="tbbo"`,
        `MBP_1="mbp-1"`, `DEFINITION="definition"`.
  - [x] Named subsets built from the enum, never re-spelled strings:
        `TICK_TIERS`, `STORED_TIERS`, `COMPANION_SCHEMAS`, `ESTIMATE_SCHEMAS`,
        exactly as Technical Decision 4 defines them.
  - [x] `SType(StrEnum)`: `RAW_SYMBOL`, `INSTRUMENT_ID`, `PARENT`,
        `CONTINUOUS` with the provider's lowercase values.
  - [x] `DeliveryMode(StrEnum)`: `BATCH_JOB="batch_job"`,
        `DIRECT_RANGE="direct_range"`.
  - [x] `CME_DATASET = "GLBX.MDP3"`; `TICK_DECODE_BATCH_BYTES = 32 * 1024 * 1024`
        with a docstring saying 226 replaces it from measurements;
        `TICK_DOWNLOAD_TIMEOUT_SECONDS` (choose a value and state the reason
        in its comment); `TICK_SPEND_CEILING_ENV = "MT_TICK_SPEND_CEILING_USD"`.
  - [x] Success: each value is defined once; module ≤ ~300 lines.
- [x] **Task 1.3: Tests for the constants** (effort: 1)
  - [x] `test/unit/data/tick/test_constants.py` (add `__init__.py` files as
        the tree needs): enum values equal the provider's spellings;
        `STORED_TIERS == {TRADES, TBBO}`; `MBP_1 ∉ STORED_TIERS`;
        `ESTIMATE_SCHEMAS` ends with `DEFINITION` and contains every tier.
  - [x] Success: the file passes.
- [x] **Task 1.4: `ProviderOutcomeUnknownError`** (effort: 1)
  - [x] Add to `providers/errors.py` as a direct subclass of `ProviderError`,
        **not** of `ProviderTransientError`. Docstring: the request may have
        been accepted and charged; reconcile before anything is resubmitted.
  - [x] Test: `issubclass(ProviderOutcomeUnknownError, ProviderError)` is
        true and `issubclass(..., ProviderTransientError)` is false; an
        `except ProviderTransientError` block does not catch it.
  - [x] Success: test passes; no other class in the file changes.
- [x] **Task 1.5: `Settings.tick_spend_ceiling_usd`** (effort: 1)
  - [x] Add `tick_spend_ceiling_usd: Decimal | None = Field(default=None, gt=0)`
        with a comment naming `MT_TICK_SPEND_CEILING_USD`.
  - [x] Success: `uv run mt --help` still works with the variable unset.
- [x] **Task 1.6: Tests for the setting** (effort: 1)
  - [x] In `test/unit/test_settings.py` style (`Settings(_env_file=None)`,
        `monkeypatch`): unset → `None`; `"12.50"` → `Decimal("12.50")`;
        `0` and `-1` → validation error naming `tick_spend_ceiling_usd`.
  - [x] Success: tests pass; no test loads `.env`.
- [x] **Task 1.7: Section 1 checkpoint** (effort: 1)
  - [x] ruff and mypy clean on touched files; unit tier passes.
  - [x] Commit: `feat: add databento dependency, tick constants, spend ceiling`.

## Section 2: Sample fixtures and the DBN file reader

Design: *Design-Time Verification Findings* (record sizes, mapping records,
real sample files), *Technical Decisions 6, 7, 11* (thread workers over the
array path, byte budget, real fixtures), *API Contracts* (`ITickFileReader`,
`ITickFile`, `RecordBatch`, `SymbolInterval`).

- [x] **Task 2.1: Copy the sample DBN files** (effort: 1)
  - [x] From `databento/dbn` at the `v0.70.0` tag, copy into
        `test/fixtures/databento/`: `test_data.{trades,tbbo,mbp-1,definition}.v3.dbn.zst`
        and `test_data.trades.v2.dbn.zst`. Fetch with
        `gh api -H "Accept: application/vnd.github.raw" repos/databento/dbn/contents/tests/data/<file>?ref=v0.70.0`.
  - [x] Write `test/fixtures/databento/SOURCES.md`: repository, tag and
        commit SHA, Apache-2.0 licence, and per file whether it is CME
        (`GLBX.MDP3`, the four ES files) or not (`definition` is `XNAS.ITCH`,
        MSFT — record-shape tests only).
  - [x] Success: each file decodes with the raw SDK (`databento.DBNStore.from_file`)
        and reports the dataset `SOURCES.md` states.
- [x] **Task 2.2: File protocols and their types in `provider.py`** (effort: 2)
  - [x] Create `data/tick/provider.py` with `ITickFileReader` and `ITickFile`
        (`Protocol`) exactly as *API Contracts* lists, plus frozen
        dataclasses `SymbolInterval(start_date, end_date, instrument_id: int)`
        and `RecordBatch(schema: TickSchema, records: numpy.ndarray, count: int)`.
  - [x] Docstrings carry the thread contract: the reader is stateless and
        shareable; each `ITickFile` belongs to one thread.
  - [x] Success: the module imports no `databento` symbol.
- [x] **Task 2.3: `databento/dbn_file.py`** (effort: 3)
  - [x] `DbnFileReader.open_file(path) -> DbnFile`; `DbnFile` fills the header
        fields from the DBN metadata: dataset, schema (as `TickSchema`),
        `stype_in` (as `SType`), start, end, `partial`, `not_found`.
  - [x] `mappings`: input symbol → tuple of `SymbolInterval`, converting the
        header's string instrument id to `int`.
  - [x] `iter_batches()`: `records_per_batch = TICK_DECODE_BATCH_BYTES // record_size`,
        where `record_size` is the itemsize of the file's record type after
        the SDK's DBN-version upgrade; yields `RecordBatch` via
        `to_ndarray(count=records_per_batch)`. Reads the budget from the
        constants module at call time so a test can patch it.
  - [x] Success: file ≤ ~300 lines; a DBN file with an unknown schema raises
        an explicit error naming it.
- [x] **Task 2.4: Tests for the file reader** (effort: 2)
  - [x] `test/unit/data/tick/test_dbn_file.py`, over the committed fixtures:
    1. Each ES file: dataset `GLBX.MDP3`, expected `TickSchema`, and
       `mappings == {"ESH1": (SymbolInterval(2020-12-28, 2020-12-29, 5482),)}`.
    2. Batches: two records total; itemsize 48 (trades) or 80 (tbbo, mbp-1);
       first record `price == 3720250000000`.
    3. The v2 trades file yields the same records and itemsize as v3.
    4. Definition file: `schema == TickSchema.DEFINITION`, itemsize 520.
    5. With `TICK_DECODE_BATCH_BYTES` patched to one record's size, a file
       yields two batches; every batch has `records.nbytes ≤` the budget.
  - [x] Success: the file passes with no network.
- [x] **Task 2.5: Section 2 checkpoint** (effort: 1)
  - [x] ruff and mypy clean; unit tier passes.
  - [x] Commit: `feat: add DBN file reader with byte-bounded batches`.

## Section 3: Metadata protocol and adapter

Design: *Technical Decisions 1, 2, 8, 10* (SDK not hand-rolled, synchronous
protocol, exclusive-end request grain, error mapping for free calls), *API
Contracts* (`ITickMetadataProvider`, constructor, context manager).

- [x] **Task 3.1: Metadata protocol and request/result types** (effort: 2)
  - [x] In `provider.py`: `ITickMetadataProvider` with the six methods from
        *API Contracts*; docstring states it is free and not thread-safe
        (one instance per caller).
  - [x] Frozen dataclasses: `TickRequest(dataset, symbols, stype_in, schema,
        start: date, end: date)` with `with_schema()` and validation that
        `end > start`; `DatasetRange`, `DayCondition`, `SymbolResolution`.
  - [x] Success: `TickRequest` docstring states `end` is exclusive.
- [x] **Task 3.2: Tests for the request type** (effort: 1)
  - [x] `with_schema` returns an equal request differing only in schema;
        `end ≤ start` raises; the dataclass is frozen.
  - [x] Success: tests pass.
- [x] **Task 3.3: Adapter skeleton, `from_settings`, context manager** (effort: 2)
  - [x] `databento/adapter.py`: `DatabentoTickProvider(client: databento.Historical,
        download_http: httpx.Client)`; `from_settings(settings)` builds both
        from `Settings.databento_api_key`, passing the key explicitly (never
        the SDK's `DATABENTO_API_KEY` fallback); a missing key raises
        `ProviderAuthError` naming `MT_DATABENTO_API_KEY`.
  - [x] `__enter__`/`__exit__`; `__exit__` closes the `httpx.Client` always.
  - [x] Success: the key is never logged.
- [x] **Task 3.4: Free-call error mapping** (effort: 2)
  - [x] One private helper used by every free method: `BentoServerError`,
        HTTP 429, connection and timeout errors → `ProviderTransientError`;
        `BentoClientError` 401/403 → `ProviderAuthError`; any other
        `BentoClientError` or a malformed response → `ProviderPermanentError`.
  - [x] Success: no free method maps errors itself.
- [x] **Task 3.5: Metadata methods** (effort: 2)
  - [x] `dataset_range`, `record_count`, `billable_size`, `cost` (SDK float
        → `Decimal` at this boundary; never pass the deprecated `mode`),
        `resolve_symbols` (always `stype_out=SType.INSTRUMENT_ID`).
  - [x] `dataset_condition(dataset, start, end)` passes the SDK
        `end_date = end - 1 day` (the one inclusive-end endpoint) and returns
        one `DayCondition` per day.
  - [x] Success: each method is one SDK call on the calling thread.
- [x] **Task 3.6: Tests for the metadata adapter** (effort: 3)
  - [x] `test/unit/data/tick/test_adapter_metadata.py` with a fake
        `Historical`-shaped object built from the SDK's documented return
        types (replaced by recordings in Task 7.3).
  - [x] `dataset_condition` for `[2025-01-06, 2025-01-11)` sends
        `end_date=2025-01-10` and returns five conditions.
  - [x] `cost` returns a `Decimal`; `resolve_symbols` sends `stype_out`
        `instrument_id`.
  - [x] Error mapping: 5xx, 429, timeout → Transient; 401, 403 → Auth;
        400 → Permanent; a malformed response (the fake returns a shape
        the adapter cannot parse, raising no `BentoError`) → Permanent.
  - [x] `from_settings` with no key → `ProviderAuthError` naming the
        variable; the context manager closes the client on normal exit and
        on an exception.
  - [x] Success: the file passes with no network and no key.
- [x] **Task 3.7: Section 3 checkpoint** (effort: 1)
  - [x] ruff and mypy clean; unit tier passes.
  - [x] Commit: `feat: add databento metadata adapter`.

## Section 4: Acquisition methods and the verified download

Design: *Technical Decisions 9, 10* (delivery modes and file-name rule,
paid-call outcomes), the failure-modes table, *API Contracts*
(`ITickAcquisitionProvider`, `BatchJob`).

- [x] **Task 4.1: Acquisition protocol and `BatchJob`** (effort: 1)
  - [x] In `provider.py`: `ITickAcquisitionProvider` with the five methods
        from *API Contracts*; docstring marks `fetch_range` and
        `submit_batch` as PAID and states the thread contract.
  - [x] Frozen `BatchJob` with the fields *API Contracts* lists, including
        `request: TickRequest` and `ts_expiration`.
  - [x] Success: `provider.py` ≤ ~300 lines.
- [x] **Task 4.2: Paid-call error mapping** (effort: 2)
  - [x] One helper for the two paid methods: 429 → Transient; 401/403 →
        Auth; any other 4xx → Permanent; **everything else** (timeout,
        connection error, 5xx, mid-stream `BentoError`) →
        `ProviderOutcomeUnknownError`.
  - [x] Success: no path from a paid method reaches `ProviderTransientError`
        except 429.
- [x] **Task 4.3: `submit_batch`, `batch_job`, `batch_jobs_since`** (effort: 2)
  - [x] `submit_batch` sends encoding `dbn`, compression `zstd`,
        `split_duration="day"`, `delivery="download"`,
        `stype_out=instrument_id`, and reads the job back through
        `get_job_details`.
  - [x] `batch_job` uses `get_job_details`; `batch_jobs_since(since)` calls
        `list_jobs(since=)` then `get_job_details` per id, rebuilding each
        job's `TickRequest`.
  - [x] Success: `list_jobs` is used only for ids.
- [x] **Task 4.4: Tests for batch submit and job reads** (effort: 2)
  - [x] Assert the exact SDK parameters `submit_batch` passes; that job reads
        go through `get_job_details`; that `batch_jobs_since` returns one
        `BatchJob` per listed id with its request.
  - [x] `submit_batch` given timeout, connection error, 5xx → each raises
        `ProviderOutcomeUnknownError` and never `ProviderTransientError`;
        429 → Transient; 400 → Permanent; 401 → Auth.
  - [x] Success: tests pass.
- [x] **Task 4.5: `fetch_range`** (effort: 2)
  - [x] Refuse an existing final `dest` up front (`FileExistsError`).
  - [x] Delete a leftover `<dest>.partial` (crash residue), stream via
        `timeseries.get_range(..., path=<dest>.partial)`, rename to `dest`
        only on clean completion.
  - [x] On any failure, delete `<dest>.partial` before raising the mapped
        error.
  - [x] Success: after any failure neither `dest` nor `<dest>.partial`
        exists.
- [x] **Task 4.6: Tests for `fetch_range`** (effort: 2)
  - [x] Timeout, connection error, 5xx, mid-stream error → each raises
        `ProviderOutcomeUnknownError`, never Transient, and leaves no file.
  - [x] 429 → Transient; other 4xx → Permanent/Auth.
  - [x] A leftover `.partial` is deleted before streaming; an existing
        `dest` raises `FileExistsError` with no SDK call made; success leaves
        only `dest`.
  - [x] Success: tests pass.
- [x] **Task 4.7: Verified `download_batch`** (effort: 3)
  - [x] Read `batch.list_files(job_id)` (URL, size, SHA-256 per file); fetch
        each with the injected `httpx.Client` (basic auth with the key,
        `timeout=TICK_DOWNLOAD_TIMEOUT_SECONDS`) into `<name>.partial`.
  - [x] Existing `.partial` vs listed size: equal → hash and rename, no
        request; smaller → resume with `Range: bytes=N-`; larger → delete
        and restart.
  - [x] A `200` to a ranged request → truncate and write from byte 0;
        `416` → delete `.partial`, Transient; 404/410 → Permanent;
        SHA-256 mismatch → delete `.partial`, Transient; timeout/connection
        → Transient, `.partial` kept.
  - [x] Rename to the final name only after size and SHA-256 match. No retry
        loop inside the adapter.
  - [x] Success: the SDK's `batch.download` is not called anywhere;
        `adapter.py` ≤ ~300 lines (extract a `_download.py` sibling inside
        `databento/` if it would exceed).
- [x] **Task 4.8: Tests for `download_batch`** (effort: 3)
  - [x] Using `httpx.MockTransport`, one test per case from *Success
        Criteria* (the `download_batch` bullet): full transfer; interrupted
        then resumed with `Range`; full-size `.partial` with no request sent;
        oversize `.partial` restarted; `200` to a ranged request; `416`;
        checksum mismatch; stalled response → Transient.
  - [x] Success: tests pass; each asserts what is left on disk.
- [x] **Task 4.9: Section 4 checkpoint** (effort: 1)
  - [x] ruff and mypy clean; unit tier passes.
  - [x] Commit: `feat: add databento acquisition methods and verified download`.

## Section 5: Estimate core and `mt data tick estimate`

Design: *Data Flow* (preflight), *Technical Decisions 3, 5* (CLI subgroup,
bundle ceiling verdict), *CLI verb*, *Special Considerations* (no billable
request, structurally).

- [x] **Task 5.1: `estimate.py`** (effort: 3)
  - [x] `build_estimate(metadata: ITickMetadataProvider, request, ceiling:
        Decimal | None) -> EstimateReport`, following *Data Flow* exactly:
        one `dataset_range` (refuse, naming the edge, if `end` is past it);
        one `dataset_condition`, tallied by condition; per schema in
        `ESTIMATE_SCHEMAS`: records, billable size, cost.
  - [x] Per stored tier: `bundle_cost_usd = tier + definition`; verdict
        `within`/`over` against the ceiling, or `no ceiling configured
        (MT_TICK_SPEND_CEILING_USD unset)` built from
        `TICK_SPEND_CEILING_ENV`. `definition` row: `bought with each tier`;
        `mbp-1` row: `not purchasable in this initiative`. Verdict strings
        defined once (enum or constants).
  - [x] `EstimateReport.to_dict()` for `--json`, including
        `ceiling_verdict`, `bundle_cost_usd`, `ceiling_usd`.
  - [x] Success: the module imports nothing from `databento/`.
- [x] **Task 5.2: Tests for the estimate core** (effort: 2)
  - [x] Fake `ITickMetadataProvider`: unset ceiling → every stored tier
        "no ceiling configured", `mbp-1` "not purchasable" regardless of the
        ceiling; a ceiling where the tier alone is within but the bundle is
        over → `over`; end past the edge → refusal naming the edge; the
        condition tally counts each day once.
  - [x] Backstop: `build_estimate` over a real `DatabentoTickProvider` whose
        fake `Historical` serves metadata and raises on any access to
        `timeseries` or `batch` completes.
  - [x] Success: tests pass.
- [x] **Task 5.3: `cli/commands/tick.py` and registration** (effort: 2)
  - [x] `tick_app` with one verb, `estimate`; options per *CLI verb*;
        `--symbols` and `--stype` required; `--stype` choices from `SType`;
        help states `--end` is exclusive; epilog states the exit-code-2
        collision with Click usage errors.
  - [x] Exit codes defined once in this module: `0` ok, `1` preflight
        (missing key, `end ≤ start`, end past the edge), `2` provider error.
  - [x] Uses `with DatabentoTickProvider.from_settings(settings) as provider:`;
        settings from `ctx.obj["settings"]`.
  - [x] Register in `data.py` beside `kalshi_app` as `name="tick"`.
  - [x] Success: `uv run mt data tick --help` lists `estimate` only.
- [x] **Task 5.4: `cli/commands/tick_render.py`** (effort: 2)
  - [x] Header: available range, requested range, day-condition tally.
        Table: one row per schema with records, billable bytes plus a human
        size, cost, bundle cost (stored tiers), verdict column.
  - [x] Built with `make_table`; `--json` goes through `print_result`.
  - [x] Success: renderer takes an `EstimateReport` only.
- [x] **Task 5.5: CLI tests** (effort: 2)
  - [x] `test/unit/cli/commands/test_data_tick.py` with `CliRunner` and a
        fake provider: happy path exits 0 with four rows; `--json` parses and
        carries the three ceiling fields; missing key → exit 1 naming
        `MT_DATABENTO_API_KEY`; `end ≤ start` → exit 1; end past edge →
        exit 1 naming the edge; provider Transient → exit 2.
  - [x] Success: tests pass with no network and no key.
- [x] **Task 5.6: Import-boundary test** (effort: 1)
  - [x] Unit test that walks `src/manta_trading` and asserts only
        `data/tick/databento/adapter.py` and `dbn_file.py` (and `_download.py`
        if Task 4.7 created it) import `databento`.
  - [x] Success: test passes.
- [x] **Task 5.7: Section 5 checkpoint** (effort: 1)
  - [x] ruff and mypy clean; unit tier passes;
        `uv run mt data tick estimate --help` shows required options.
  - [x] Commit: `feat: add mt data tick estimate cost preflight`.

## Section 6: Decode benchmark

Design: *Technical Decisions 6, 12* (thread workers over the array path;
benchmark input is real records and is not a correctness fixture).

- [x] **Task 6.1: `scripts/bench_dbn_decode.py`** (effort: 2)
  - [x] Builds its input in memory: the real trades header followed by the
        real trade records repeated to `--records`; decodes through the
        per-record path and the `to_ndarray` array path at 1 and
        `--threads` threads; prints wall time, records/s, speedup.
  - [x] Docstring states the throughput caveat: decode only, repeated
        records, no `COPY` — an upper bound that can fail the architecture's
        ingest target, never pass it; 226 decides.
  - [x] Success: `uv run python scripts/bench_dbn_decode.py --records 2000000 --threads 4`
        completes and prints the table.
- [x] **Task 6.2: Run and record the benchmark** (effort: 1)
  - [x] Paste the output verbatim under *Recorded Results* below, with the
        host and Python version.
  - [x] State in one line whether the array path showed a multi-thread
        speedup and the per-record path did not. If not, stop and report to
        the PM: Technical Decision 6 rests on it.
  - [x] Commit: `feat: add DBN decode benchmark and record results`.

## Section 7: Recorded fixtures, documentation, live walkthrough

Needs `MT_DATABENTO_API_KEY` in the dev `.env` (PM prerequisite). Design:
*Technical Decision 11* (fixtures recorded, never invented), *Technical
Requirements* (docs), *Verification Walkthrough*.

- [x] **Task 7.1: `scripts/record_databento_fixtures.py`** (effort: 2)
  - [x] Modelled on `record_kalshi_fixtures.py`. Builds a
        `DatabentoTickProvider` via `from_settings`, types it as
        `ITickMetadataProvider`, and records each metadata method's raw SDK
        response for `ES.c.0`, `continuous`, `2025-01-06`–`2025-01-11`
        across `ESTIMATE_SCHEMAS` into `test/fixtures/databento/metadata/*.json`.
  - [x] Imports `ESTIMATE_SCHEMAS`; calls nothing outside the metadata
        protocol.
  - [x] Success: script runs clean; no key or header value appears in any
        written file.
- [x] **Task 7.2: Record and commit the fixtures** (effort: 1)
  - [x] Run the script; inspect each JSON file for secrets; commit.
  - [x] Commit: `test: add recorded databento metadata fixtures`.
- [x] **Task 7.3: Re-point metadata-shaped tests at the recordings** (effort: 2)
  - [x] Replace every hand-built metadata response in the adapter (3.6),
        estimate (5.2), and CLI/renderer (5.5) tests with a loader over the
        recorded JSON.
  - [x] Success: `grep` finds no hand-written metadata response left in
        `test/unit/data/tick` or `test_data_tick.py`; all tests pass.
- [x] **Task 7.4: Record account facts** (effort: 1)
  - [x] From the account (or a free SDK call where one exists), record
        under *Recorded Results*: the batch retention window and any
        per-mode (batch, direct) size limits. If a figure cannot be found,
        write "not published" with where you looked — never a guessed value.
  - [x] Beside each figure, name its consumer: the retention window feeds
        223's retention check (the window must cover several consecutive
        missed firings at 223's cadence); the size limits feed 223's
        batch-versus-direct delivery choice.
  - [x] Copy both figures into the design's findings table (the "Delivery
        modes" and "Batch retention window" rows) so a 223 author reading
        only the design finds them.
- [x] **Task 7.5: README and `.env_sample`** (effort: 2)
  - [x] Add `tick` to the `mt data …` row of the CLI map.
  - [x] New `### Tick / Databento` environment table: `MT_DATABENTO_API_KEY`,
        `MT_TICK_SPEND_CEILING_USD`, `MT_TICK_DB_URL` (present; no consumer
        until 222).
  - [x] New `## Futures tick data` section skeleton with the `estimate`
        command, exclusive `--end`, and exit codes.
  - [x] `.env_sample`: both new variables under `--- Optional ---`.
  - [x] Success: every env name matches its code spelling.
- [x] **Task 7.6: Data-correctness contract rows** (effort: 1)
  - [x] In `user/reference/data-correctness-architecture.md`, the slice
        mapping table: the tooling-consistency row (I10) gains "220 fixes the
        tick shape: `mt data tick` subgroup, verb vocabulary as I10"; the
        loud-failure row (I9) cites the preflight's refusals (missing key,
        end past the edge, unknown-outcome paid calls).
  - [x] Commit: `docs: add tick CLI, env table, and contract rows for 220`.
- [x] **Task 7.7: Live verification walkthrough** (effort: 2)
  - [x] Run *Verification Walkthrough* steps 1–7 from the slice design.
        Paste step 6's table and step 7's JSON verdicts under *Recorded
        Results*.
  - [x] Check: `trades` and `tbbo` record counts equal, sizes differ;
        `mbp-1` record count much larger; verdicts split correctly at the
        step 7 ceiling.
  - [x] Step 8: open the Databento portal usage page and confirm no batch
        jobs and no charges today; record the observation.
  - [x] Success: all seven steps pass and step 8 shows zero spend.
- [x] **Task 7.8: Final validation** (effort: 1)
  - [x] Full unit tier passes; ruff and mypy clean on all touched files;
        every source file ≤ ~300 lines.
  - [x] Review the *Success Criteria* list in the slice design item by item
        and confirm each is met.
  - [x] Commit: `docs: record 220 walkthrough results`.

## Recorded Results

Filled in by Tasks 6.2, 7.4, and 7.7.

- **Decode benchmark (Task 6.2):** run 2026-09-27 on manta9000 (32 cores),
  Python 3.12.9, databento 0.87.0, databento-dbn 0.70.0. Verbatim output of
  `uv run python scripts/bench_dbn_decode.py --records 2000000 --threads 4`:

  ```
  host: manta9000  python: 3.12.9  databento: 0.87.0
  records per decode: 2,000,000  threads: 1 vs 4

  path        threads   wall s      records/s  speedup
  per-record        1     0.35      5,783,648    1.00x
  per-record        4     1.41      5,685,388    0.98x
  array             1     0.06     34,159,784    1.00x
  array             4     0.07    108,545,860    3.18x
  ```

  A repeat at 2M gave 0.96x / 3.16x. At `--records 20000000` (array wall
  times long enough to be stable): per-record 1 thread 3.39 s
  (5,892,652/s), 4 threads 14.25 s (0.95x); array 1 thread 0.58 s
  (34,412,534/s), 4 threads 0.62 s (128,746,035/s, 3.74x).

  **Verdict:** the array path showed a multi-thread speedup (3.2–3.7x at 4
  threads) and the per-record path did not (0.95–0.98x) — Technical
  Decision 6 holds. Caveat (TD 12): decode only, repeated records, no `COPY`
  — an upper bound that can fail the ingest target, never pass it; 226
  decides.
- **Batch retention window (Task 7.4):** **30 days after the job finishes
  processing.** Measured 2026-09-27 from the account with free calls
  (`batch.list_jobs` + `get_job_details`): on all 7 of the account's jobs
  (2024-07 to 2025-01, GLBX.MDP3 and XNAS.ITCH), `ts_expiration −
  ts_process_done` is exactly 30 days (for example `GLBX-20250123-XT4GD5UM6C`:
  done 2025-01-23T05:18:12.939863Z, expires 2025-02-22T05:18:12.939863Z). All
  7 are now `expired`. Consumer: **223's retention check.** The window must
  cover several consecutive missed firings at 223's cadence. At one firing a
  day, 30 days covers about 29 missed firings. 223 still reads each unit's own
  `ts_expiration` and never this constant.
- **Per-mode size limits (Task 7.4):** **not published** in any source
  readable here. Looked in: the SDK v0.87.0 source (`batch.py`,
  `timeseries.py`, `common/http.py`: no size check or documented cap), the
  SDK changelog and quickstart notebook, and Databento's blog ("streaming for
  small on-demand work; batch for larger requests, typically over 5 GB" is
  guidance, not a limit). The account API exposes no limit, and the docs site
  is client-rendered and unreadable by this tooling. Consumer: **223's
  batch-versus-direct delivery choice**. Until a limit is found, 223 should
  prefer batch jobs (re-downloadable free for 30 days; a repeated stream is
  billed again). Open item for the PM: check the portal or docs for a
  published per-request limit.
- **Live estimate, ES.c.0 2025-01-06 → 2025-01-11 (Task 7.7, step 6):** run
  2026-09-27, 5 available days, ceiling unset:

  ```
  Schema     Records     Billable bytes  Size       Cost (USD)  Bundle   Verdict
  trades     2,509,722   120,466,656     114.9 MiB  $3.14       $3.14    no ceiling configured (MT_TICK_SPEND_CEILING_USD unset)
  tbbo       2,509,722   200,777,760     191.5 MiB  $5.24       $5.24    no ceiling configured (MT_TICK_SPEND_CEILING_USD unset)
  mbp-1      33,907,169  2,712,573,520   2.5 GiB    $4.55       —        not purchasable in this initiative
  definition 5           2,600           2.5 KiB    <$0.01      —        bought with each tier
  ```

  Checks: `trades` and `tbbo` have equal record counts (2,509,722) and
  different sizes; `mbp-1` has 13.5× the records. Note for the tier decision:
  `mbp-1` prices **below** `tbbo` for this range ($4.55 vs $5.24) despite
  13.5× the bytes. For scale: `ES.FUT` (parent), 2026-03-27 → 2026-09-27, is
  `tbbo` $127.48 (bundle), `trades` $76.49, `mbp-1` $160.87 (89.4 GiB); 181
  available and 3 degraded days.
- **Ceiling verdicts (Task 7.7, step 7):** `MT_TICK_SPEND_CEILING_USD=4.00`,
  `--json`: `ceiling_usd "4.00"`; trades bundle `3.141416970641` →
  `within`; tbbo bundle `5.235692206770` → `over`; mbp-1 `not purchasable in
  this initiative`; definition `bought with each tier`. Step 5 (end
  2030-01-01): exit 1, `requested end 2030-01-01 (exclusive) is past the
  dataset's available end 2026-09-27T12:41:28.771893+00:00`.
- **Portal usage check (Task 7.7, step 8):** API side confirmed:
  `batch.list_jobs(since=2026-09-27T00:00Z)` returns 0 jobs. The charges side
  needs the PM to look at the Databento portal's usage page. **Confirmed by
  the PM 2026-09-27: zero usage.** Nothing was bought.
