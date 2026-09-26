---
docType: slice-design
slice: databento-adapter-and-cost-preflight
project: trading
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [902]
interfaces: [222, 223, 224, 226, 229, 230]
effort: 3
dateCreated: 20260925
dateUpdated: 20260925
status: not_started
---

# Slice Design: Databento Adapter and Cost Preflight (220)

## Overview

First slice of initiative 220 (futures tick data). It adds the `databento` dependency, a tick-provider protocol shaped like `IMinuteDataProvider` and `IDailyDataProvider`, and the one adapter that owns Databento's wire format: DBN decode as a bounded-batch iterator, the four symbologies, and both delivery modes (batch job and direct range request, each landing a file). It ships the first tick CLI verb, `mt data tick estimate`, a cost-and-size preflight that calls only free metadata endpoints and reports the three candidate tiers side by side for one instrument set and range. It adds `MT_TICK_SPEND_CEILING_USD` to `Settings`.

The slice buys nothing. No tick database, no migration, no table. It also fixes two decisions the architecture assigned to this design: the CLI/API surface shape (an `mt data tick` subgroup and a `/api/v1/futures/*` namespace — Technical Decision 3) and the adapter's execution model (worker threads over the SDK's array-batch decode path — Technical Decision 6).

## Design-Time Verification Findings

The architecture marks several facts "verify at slice design". Each was checked on 2026-09-25 against the `databento` Python SDK source (v0.87.0, released 2026-09-22), the `databento/dbn` repository (v0.70.0), and the real sample files in that repository, decoded locally on Python 3.12.9. Databento's documentation site is client-rendered and could not be read by the tooling available in this session, so where a fact comes only from those pages it is marked **unresolved** and carried into a task.

| Item | Finding | Source |
|---|---|---|
| Free metadata endpoints | `metadata.get_cost`, `get_billable_size`, `get_record_count`, `get_dataset_range`, `get_dataset_condition`, `list_unit_prices`, and `symbology.resolve` all exist in the SDK. `get_record_count` is a POST taking dataset, symbols (up to 2,000 per request), schema, start, **exclusive** end, and `stype_in`. `get_cost`'s `mode` parameter is deprecated and must not be passed. | `databento/historical/api/metadata.py`, `symbology.py` |
| Availability edge | `get_dataset_range(dataset)` returns the available range; `get_dataset_condition(dataset, start_date, end_date)` returns one dict per date (date, condition, last modified). Databento's historical API serves data "older than 24 hours"; a request past the edge is rejected with the available end in the message. | SDK docstrings; databento.com/historical |
| Delivery modes | `timeseries.get_range(..., path=)` streams a request to a file. `batch.submit_job(dataset, symbols, schema, start, end, encoding, compression, split_symbols, split_duration ∈ {day, week, month, year, none}, split_size, delivery='download', stype_in, stype_out, limit)`. Databento's guidance: streaming for small on-demand work, batch for "larger data requests, typically over 5 GB"; a repeated stream request is billed again, a batch file can be re-downloaded without charge inside its window. No hard size limit on either mode was found. | `batch.py`, `timeseries.py`; Databento blog "streaming vs. batch download" |
| Batch retention window | The window exists: job states are `queued`, `processing`, `done`, `expired`, and the job record carries `ts_expiration`. **The published length is unresolved** (docs unreadable here). Design consequence: the manifest deadline is always the job's own `ts_expiration`, never a constant; the published figure is recorded as a task once the PM's account exists. | `batch.py` |
| Batch API change in flight | `batch.list_jobs` is being reduced to `id`, `state`, `ts_received`; full details move to `batch.get_job_details(job_id)`, present in SDK 0.87.0. The adapter uses `get_job_details` from the start. | Databento blog "Upcoming changes to the historical batch API" (2026-08) |
| Symbology | `stype_in` ∈ {`raw_symbol`, `instrument_id`, `parent`, `continuous`}; `stype_out` defaults to `instrument_id`. `symbology.resolve(dataset, symbols, stype_in, stype_out, start_date, end_date)` is free. | `symbology.py` |
| Mapping records in delivered files | Historical files carry no `SymbolMappingMsg` records — that type is documented as "a symbol mapping message from the live API". Mappings live in the DBN **metadata header**: `mappings` (input symbol → list of `{start_date, end_date, symbol}` intervals, where `symbol` is the instrument id as a string when `stype_out=instrument_id`), plus `partial` and `not_found` symbol lists. Confirmed on the sample files: `{'ESH1': [{2020-12-28, 2020-12-29, '5482'}]}`. The Python `Metadata` object exposes `mappings` as a dict; the Rust-side `symbol_map_for_date` helper is not exposed in Python. | `rust/dbn/src/record.rs`, `metadata.rs`; local decode |
| Record sizes | `TradeMsg` (schema `trades`) is 48 bytes; `Mbp1Msg` (schemas `tbbo` and `mbp-1`) is 80 bytes; `InstrumentDefMsg` v3 is 520 bytes. Fields as delivered: `ts_event`, `ts_recv`, `rtype`, `publisher_id`, `instrument_id`, `price` (fixed-point, 1e-9), `size`, `action`, `side`, `flags`, `depth`, `ts_in_delta`, `sequence`; tbbo adds `bid_px_00`, `ask_px_00`, `bid_sz_00`, `ask_sz_00`, `bid_ct_00`, `ask_ct_00`. | `size_hint` and `to_ndarray` dtype on the sample files |
| Bounded batch decode | `DBNStore.to_ndarray(count=N)` yields NumPy structured arrays of at most N records, one record per row at the native record size, over a zstd stream reader. This is the bounded-batch iterator the architecture asks for, provided by the SDK. | `databento/common/dbnstore.py`; local decode |
| Decoder and the interpreter lock | `DBNDecoder.decode()` and `write_and_decode()` in `python/src/dbn_decoder.rs` take the `Python` token for their whole body and never call `allow_threads`/`detach`: the per-record decoder **holds the GIL** on CPython 3.12. (`#[pymodule(gil_used = false)]` only matters on free-threaded builds.) The `to_ndarray` path does not use that decoder: it decompresses through `python-zstandard`, which releases the GIL during decompression (documented as an implementation detail), and views the bytes with `numpy.frombuffer`. | `dbn` v0.70.0 source; python-zstandard docs |
| Real sample files | `databento/dbn` `tests/data/test_data.{trades,tbbo,mbp-1}.v3.dbn.zst` are real `GLBX.MDP3` records: symbol `ESH1`, instrument id 5482, two records each from 2020-12-28, prices `3720250000000` (= 3720.25). `test_data.definition.v3.dbn.zst` is a real `definition` record but from `XNAS.ITCH` (MSFT) — valid for decode-shape tests, not a CME definition. v1/v2 variants exist for DBN-version compatibility. Apache-2.0. | local decode |
| SDK footprint | `databento` 0.87.0 pulls `databento-dbn` 0.70.0, `numpy`, `pandas` (<4), `pyarrow` (a 48 MiB wheel), `zstandard`, `requests`, `aiohttp`. Installs cleanly on Python 3.12.9. The `Historical` client reads `DATABENTO_API_KEY` from the process environment when no key is passed — the adapter always passes the key explicitly. Errors: `BentoError` → `BentoHttpError(http_status)` → `BentoClientError` (4xx) / `BentoServerError` (5xx). | `pyproject.toml`, `client.py`, `common/error.py` |

## Value

Architectural enablement plus one operator-facing tool:

- **Every later 220 slice stands on the protocol and adapter.** 223 (acquisition) submits, polls, and downloads through it; 224 (ingest) decodes through its bounded iterator; 226 (proof) measures its numbers.
- **The tier decision gets its input.** The PM chooses `trades` versus `tbbo` (and whether GC follows) from `mt data tick estimate`'s side-by-side figures for the same instrument and range — the architecture's "schema tier is the cost decision" made from measured sizes, not projections.
- **Two architecture-level decisions are closed** so 222–230 do not each re-open them: the CLI/API surface shape and the threads-versus-processes execution model.
- **The spend ceiling exists before anything can spend.** 223's guard reads a setting that already has tests and documentation.

## Technical Scope

**In scope:**

- `databento` added to `pyproject.toml` dependencies and `uv.lock` (production installs with `uv sync --frozen`).
- New package `src/manta_trading/data/tick/`: the tick-provider protocol and its request/result types, the tick constants module (tier and delivery-mode enums, dataset code, batch bound, env-name constants, exit codes' home), and the preflight core.
- New subpackage `src/manta_trading/data/tick/databento/`: the adapter (protocol implementation over the SDK's `Historical` client, error mapping) and the DBN file reader (header mappings, bounded batches).
- `Settings.tick_spend_ceiling_usd` (`MT_TICK_SPEND_CEILING_USD`), `Decimal | None`, `> 0` when set, no default value.
- New CLI group `mt data tick` (`cli/commands/tick.py`) with one verb, `estimate`, and its Rich renderer (`cli/commands/tick_render.py`); `--json` on the verb.
- Real DBN fixtures under `test/fixtures/databento/` (copied from `databento/dbn` with provenance), recorded metadata-response fixtures for the free endpoints, and the recording script `scripts/record_databento_fixtures.py`.
- A bounded decode microbenchmark, `scripts/bench_dbn_decode.py`, that answers the interpreter-lock question empirically for the two decode paths.
- Documentation: README CLI map and a new environment table, `.env_sample`, and the 220 row in the data-correctness contract's slice-mapping table.

**Out of scope:** any billable request; the manifest, ledger, and definitions tables (222); the acquisition and ingest passes and the spend-ceiling *guard* (223); the `COPY` writer (224); `PassKind.TICK` and `schedule_for` (223); production environment files and systemd units (225); any API endpoint (230); the CME session model (221); a Databento entry in `mt provider test` beyond what the registry already does.

## Dependencies

### Prerequisites

- Slice 902 (provider registry, complete): `ProviderType.DATABENTO` and the `databento` `ProviderProfile` (`api_key_env="MT_DATABENTO_API_KEY"`, aliases `db`, `bento`) already exist in `providers/types.py` and `providers/profiles.py`. No registry change is needed.
- `Settings.databento_api_key` (`MT_DATABENTO_API_KEY`) already exists in `config/__init__.py`.
- **PM action, before fixture recording and the verification walkthrough:** create the Databento account, set the provider-side account budget (the architecture's second spend guard), and put `MT_DATABENTO_API_KEY` in the dev `.env`. Nothing in this slice needs the key to build or to pass unit tests; recording the metadata fixtures and running `estimate` live do.
- Network access to `hist.databento.com` for the recording script and the walkthrough only.

### Interfaces Required

- `manta_trading.providers.errors` — `ProviderError`, `ProviderAuthError`, `ProviderTransientError`, `ProviderPermanentError`; reused, not extended.
- `manta_trading.cli.output` — `print_result`, `print_error`, `make_table`; the `--json` convention every verb follows.
- `manta_trading.config.Settings` — loaded once in `cli/app.py`'s callback and read from `ctx.obj["settings"]`, as `mt data kalshi` does.
- `data/kalshi/constants.py` and `cli/commands/kalshi.py` as the pattern for a per-domain constants module and a source subgroup with integer exit codes defined once.

## Architecture

### Component Structure

```
src/manta_trading/data/tick/
  __init__.py
  constants.py            # TickTier, STORED_TIERS, DeliveryMode, CME_DATASET,
                          # TICK_DECODE_BATCH_RECORDS, env-name constants,
                          # ESTIMATE_SCHEMAS — every tick comparison value, once
  provider.py             # ITickDataProvider (Protocol) + frozen request/result
                          # dataclasses: TickRequest, CostEstimate, DatasetRange,
                          # DayCondition, SymbolResolution, BatchJob, RecordBatch
  estimate.py             # build_estimate(provider, request, ceiling) -> EstimateReport
                          # pure over the protocol; the CLI renders it
  databento/
    __init__.py
    adapter.py            # DatabentoTickProvider: the protocol over db.Historical;
                          # from_settings(); Bento* -> Provider* error mapping
    dbn_file.py           # DbnFile: header (dataset, schema, stype, mappings,
                          # partial, not_found) + iter_batches(count) -> RecordBatch

src/manta_trading/cli/commands/tick.py          # tick_app; `estimate`; exit codes
src/manta_trading/cli/commands/tick_render.py   # Rich table for the estimate report

test/fixtures/databento/*.dbn.zst               # real GLBX.MDP3 sample files (+ SOURCES.md)
test/fixtures/databento/metadata/*.json         # recorded free-endpoint responses
scripts/record_databento_fixtures.py            # free calls only; needs the key
scripts/bench_dbn_decode.py                     # per-record vs array path, 1 vs N threads
```

The adapter is the only module that imports `databento`. `dbn_file.py` is the only module that reads a DBN byte. Everything above the protocol — the estimate core, the CLI, and every later pass — sees `TickRequest`, `RecordBatch`, and the other frozen dataclasses, never an SDK type.

### Data Flow

Preflight (this slice's only runtime path):

```
mt data tick estimate --symbols … --stype … --start … --end … [--json]
  → Settings (key, ceiling) → DatabentoTickProvider.from_settings
  → build_estimate(provider, TickRequest, ceiling):
      dataset_range()                          # once; refuse if end > edge, naming the edge
      dataset_condition(start, end)            # once; tally available/pending/degraded/missing
      for schema in ESTIMATE_SCHEMAS:          # trades, tbbo, mbp-1, definition
          record_count(request@schema)
          billable_size(request@schema)
          cost(request@schema)
      compare each cost with the ceiling       # set: within/over; unset: "no ceiling configured"
  → EstimateReport → tick_render (Rich) or print_result(to_dict(), json_mode=True)
```

Decode (exercised by tests and the benchmark here; by 224 in production):

```
archive file → DbnFile.open(path)
  → header: dataset, schema, stype_in/out, start, end, symbols, mappings, partial, not_found
  → iter_batches(TICK_DECODE_BATCH_RECORDS) → RecordBatch(tier, records: ndarray, count)
```

Every metadata call is one SDK call, made synchronously, on the calling thread. The preflight is a plain Typer command with no event loop; it needs none.

### State Management

None persisted. The preflight holds one `EstimateReport` in memory and prints it. `DbnFile` owns its file handle and zstd reader for the life of one iteration; nothing is shared between iterations or threads. The setting is read once per process.

## Technical Decisions

1. **The `databento` SDK, not a hand-rolled httpx client.** The SDK provides the DBN decoder, the zstd stream handling, the array-batch iterator, batch-download with bounded retries (`BATCH_DOWNLOAD_MAX_RETRIES = 5`), and the symbology helpers — all of which this initiative needs and none of which the project should re-implement. Cost: a heavier install (pyarrow, pandas, requests, aiohttp alongside the project's httpx). Accepted; the footprint is recorded in the findings table. The SDK's environment-variable fallback (`DATABENTO_API_KEY`) is never relied on — the key always comes from `Settings` (python rules: one value, one source).

2. **A synchronous protocol; callers move it off the loop.** The SDK is blocking (`requests`), and the architecture fixes the iterator as synchronous. So `ITickDataProvider` is synchronous throughout — metadata, submit, poll, download, decode — and an async caller (the 223/224 passes, which follow the Kalshi async phase contract) wraps each call in `asyncio.to_thread`, the project's existing pattern for the Kalshi signer, the JSONL sink, and the orchestrator's chunk writer. Rejected alternative: an `async def` protocol that hides `to_thread` inside the adapter. That makes every method look cheap on the loop while blocking underneath, contradicting the python rules' "<1 ms of synchronous work inside `async def`" and putting the executor decision in the wrong layer.

3. **CLI: an `mt data tick` subgroup. API: a `/api/v1/futures/*` namespace. One decision, made here for the whole initiative.** Futures identity (contract, product, roll method) has no analogue in the granularity-switch surface: `data status` takes `--daily/--minute` booleans, `data get` and `data pull` take positional tokens from `constants.Granularity` (`1m`, `1d`), and none of those can carry "which contract under which rule". Kalshi took a subgroup for the same reason. The data-correctness contract's tooling-consistency invariant (I10, "every granularity supports the same operator-facing surface") is *written* in subgroup form — `mt data tick {status, ingest, daemon, coverage, backfill}` — so the subgroup is its literal shape, and the requirement it actually imposes is the verb vocabulary, which the subgroup adopts: `status`, `coverage`, `pass` (the daemon-equivalent, in the Kalshi shape), `ingest`, `backfill`, `debug`, plus `estimate` (this slice) and `get` (229). The API mirrors the choice after 188's Kalshi precedent: `/api/v1/futures/*` with contract and roll-method parameters, rather than tick as a granularity on `/api/v1/bars/{symbol}`. Composed surfaces (`mt data overview`, `health`, `accounting`, `/api/v1/status`, `/api/v1/overview`) gain tick lines in place under either choice and are unaffected. 229 and 230 apply this decision; they do not revisit it.

4. **The tier enum lands here, not in 222.** The plan's 222 entry says the storage track "adds the tier enum"; the preflight needs the vocabulary now, and a value used in a conditional is defined once. `TickTier(StrEnum)` = `TRADES="trades"`, `TBBO="tbbo"`, `MBP_1="mbp-1"` — the provider's own schema names, so a tier is also a request parameter. `STORED_TIERS: frozenset[TickTier] = {TRADES, TBBO}` names the tiers this initiative stores; `mbp-1` is estimate-only. 222 renders its manifest and ledger CHECK constraints from `STORED_TIERS`. The `definition` schema is not a tier: it is a companion schema the preflight also prices, because 223 buys it and the cost picture is incomplete without it. `ESTIMATE_SCHEMAS = (trades, tbbo, mbp-1, definition)` is the one tuple the preflight iterates and the one place a reviewer checks that nothing billable is on the list.

5. **`MT_TICK_SPEND_CEILING_USD` is `Decimal | None = None`, validated `> 0` when set.** "No default" means no default *value*: unset is the state "no ceiling configured", which 223 treats as "refuse to purchase", not as a number. Every `Settings` field today is optional in this way and `Settings()` is built for every `mt` command, so a pydantic-required field would break `mt --help`. Money is `Decimal` for the same reason Kalshi parses fixed-point strings into `Decimal` — exactness — and the SDK's float cost is converted at the adapter boundary. The env name is spelled once: `TICK_SPEND_CEILING_ENV = "MT_TICK_SPEND_CEILING_USD"` in `data/tick/constants.py`, cited by the renderer and tests. The preflight reports the ceiling's state next to every tier's cost so the operator sees the comparison 223's guard will make.

6. **Execution model: worker threads over the array-batch path; the per-record decoder is not used for ingest.** The findings settle the architecture's open question on two facts. The SDK's per-record decoder holds the GIL, so threads running it would be no faster than the loop. The SDK's `to_ndarray(count=N)` path holds the GIL only for the `frombuffer` view; decompression runs with the lock released. It also produces one contiguous array per batch at the native record size instead of N Python objects, which is what makes a stated memory budget meaningful. Therefore: `RecordBatch.records` is a NumPy structured array in the provider's own field layout, `DbnFile.iter_batches` is `to_ndarray(count=TICK_DECODE_BATCH_RECORDS)` behind the protocol, and the worker that decodes a unit is a **thread**. The process fallback the architecture describes is kept available by construction — the iterator is a synchronous generator with no shared state, so it runs unchanged in a thread or a subprocess — and is adopted only if 226 measures otherwise. The benchmark in this slice confirms the direction on sample data; 226 measures it on purchased volume. State review for the executor (python rules): `DbnFile` owns its handle and reader; the SDK client is not shared across workers — 223/224 construct one per worker.

7. **The batch bound is one constant with a stated budget.** `TICK_DECODE_BATCH_RECORDS = 250_000` in `data/tick/constants.py`, with a docstring deriving it: at the verified 80 bytes per `tbbo` record a batch is 20 MiB native (12 MiB for `trades`, 130 MiB for a `definition` batch — definitions are small in count, so the same bound is fine), against a stated budget of one in-flight batch ≤ 32 MiB per worker. 226 replaces the number from measurements and rewrites the docstring; nothing else changes.

8. **The request grain is calendar-day UTC with an exclusive end, matching the provider.** `TickRequest(dataset, symbols, stype_in, schema, start: date, end: date)`; `end` is exclusive because `get_record_count`/`get_cost`/`submit_job` treat it so, and mismatching the provider's own convention is how a day goes missing (memory: FastAPI's date/datetime union dropped a day the same way). The CLI help states it. A `TickRequest` is immutable; `with_schema()` returns the same request at another schema, which is how the preflight guarantees "the same instrument and range" across tiers.

9. **Both delivery modes land a file, and the vocabulary that names them lives with the adapter.** `DeliveryMode(StrEnum)` = `BATCH_JOB="batch_job"`, `DIRECT_RANGE="direct_range"`. `fetch_range(request, dest)` streams a direct request to `dest` via `get_range(path=)`; `submit_batch(request)` → `BatchJob(id, state, ts_expiration, record_count, billed_size, cost_usd, …)` read back through `get_job_details`; `download_batch(job_id, dest_dir)` → files. 222's manifest discriminator consumes the enum; the realtime initiative adds its own member later. The batch job's `ts_expiration` is the only source of a unit's download deadline.

10. **Errors are mapped once, at the adapter boundary, onto the existing taxonomy.** `BentoServerError` and `requests` connection/timeout errors → `ProviderTransientError`; `BentoClientError` with status 401/403 → `ProviderAuthError`; any other `BentoClientError` or a malformed response → `ProviderPermanentError`; a missing key at `from_settings` → `ProviderAuthError` naming `MT_DATABENTO_API_KEY`. No retry loop of our own around metadata calls in this slice: the preflight is interactive and a transient failure is reported with exit code 2 (provider), following Kalshi's exit-code pattern with the integers defined once in `tick.py`.

11. **Fixtures are real or recorded, never invented.** Decode tests run over the `databento/dbn` sample files (v3, plus one v2 to prove the reader does not assume the current DBN version), copied with a `SOURCES.md` naming the repository, commit, and Apache-2.0 licence. Metadata-response fixtures are recorded by `scripts/record_databento_fixtures.py` from the free endpoints only (the script imports `ESTIMATE_SCHEMAS` and the metadata method names and touches nothing else) and committed as JSON; the renderer and the estimate core are tested on those shapes. The `XNAS.ITCH` definition sample is used for record-shape tests only and is labelled as non-CME in `SOURCES.md`.

12. **The benchmark input is real records, and is not a correctness fixture.** The sample files hold two records each — enough to prove decode, useless for throughput. `bench_dbn_decode.py` builds its input in memory by writing a real header followed by the real trade records repeated to a stated count, decodes it through both paths in 1 and N threads, and prints wall time and speedup. Its output goes in the task file as the empirical answer beside the source-inspection answer; it is never used to assert parsing.

## Implementation Details

### API Contracts

**Protocol** (`data/tick/provider.py`), synchronous throughout:

```python
class ITickDataProvider(Protocol):
    def dataset_range(self, dataset: str) -> DatasetRange: ...
    def dataset_condition(self, dataset: str, start: date, end: date) -> tuple[DayCondition, ...]: ...
    def record_count(self, request: TickRequest) -> int: ...
    def billable_size(self, request: TickRequest) -> int: ...          # bytes, uncompressed
    def cost(self, request: TickRequest) -> Decimal: ...               # USD
    def resolve_symbols(self, request: TickRequest, stype_out: str) -> SymbolResolution: ...
    def fetch_range(self, request: TickRequest, dest: Path) -> Path: ...
    def submit_batch(self, request: TickRequest) -> BatchJob: ...
    def batch_job(self, job_id: str) -> BatchJob: ...                  # get_job_details
    def download_batch(self, job_id: str, dest_dir: Path) -> tuple[Path, ...]: ...
    def open_file(self, path: Path) -> DbnFile: ...
```

`DbnFile` exposes the header fields listed under Component Structure and `iter_batches(count: int) -> Iterator[RecordBatch]`. `RecordBatch` is frozen: `tier: TickTier`, `records: numpy.ndarray` (structured, provider field names), `count: int`. `BatchJob` carries `job_id`, `state`, `ts_received`, `ts_expiration`, `record_count`, `billed_size`, `actual_size`, `package_size`, `cost_usd` — the fields 222's manifest row is written from.

**CLI verb:**

```
mt data tick estimate --symbols <csv> --stype {raw_symbol|instrument_id|parent|continuous}
                      --start YYYY-MM-DD --end YYYY-MM-DD [--json]
```

`--symbols` and `--stype` are both required — a cost tool that guesses the symbol set is a cost tool that is wrong by an order of magnitude, silently. `--end` is exclusive (stated in help). Output, per schema in `ESTIMATE_SCHEMAS`: records, billable bytes (and a human-readable size), cost in USD, and the ceiling verdict (`within`, `over`, or `no ceiling configured (MT_TICK_SPEND_CEILING_USD unset)`). Above the table: dataset available range, the requested range, and the day-condition tally. Exit codes, defined once in `tick.py`: `0` ok; `1` preflight (missing key, invalid arguments, requested end past the available edge — the message names the edge); `2` provider error. `--json` emits `EstimateReport.to_dict()`.

**Settings:**

```python
tick_spend_ceiling_usd: Decimal | None = Field(default=None, gt=0)   # MT_TICK_SPEND_CEILING_USD
```

## Integration Points

### Provides to Other Slices

- **222 (storage track):** `TickTier`, `STORED_TIERS`, `DeliveryMode`, and the `BatchJob` field set the manifest row is modelled on.
- **223 (acquisition pass):** `cost`, `billable_size`, `record_count`, `dataset_range`, `dataset_condition`, `resolve_symbols`, `submit_batch`, `batch_job`, `download_batch`, `fetch_range`; `Settings.tick_spend_ceiling_usd`; the exclusive-end request grain; the rule that a unit's deadline is `BatchJob.ts_expiration`.
- **224 (ingest pass):** `open_file`, `DbnFile.mappings/partial/not_found`, `iter_batches`, `RecordBatch`, `TICK_DECODE_BATCH_RECORDS`, and the thread-worker decision.
- **226 (proof):** the batch constant and its docstring to rewrite; the benchmark script to re-run on purchased data; the mapping-completeness check is defined against `DbnFile.mappings` (every `instrument_id` in a file has an interval covering its date, else the raw-symbol fallback — `stype_in=raw_symbol`, already a request parameter — is adopted).
- **229 / 230:** the subgroup and namespace decision (Technical Decision 3) and the verb vocabulary.

### Consumes from Other Slices

- The provider registry (902) as it stands. If the `databento` profile's `api_key_env` ever changes, `from_settings` reads `Settings.databento_api_key` and would need the same change — the name is spelled in `profiles.py` and `config/__init__.py` today, and this slice adds no third copy.
- `cli/output.py` helpers; a change to the `--json` convention there applies here automatically.

## Success Criteria

### Functional Requirements

- `mt data tick estimate` with a valid key prints one row per schema in `ESTIMATE_SCHEMAS` with records, billable size, cost, and the ceiling verdict, and exits 0.
- With `MT_DATABENTO_API_KEY` unset it exits 1 and the message names the variable. With `--end` past the dataset's available end it exits 1 and the message states the available end. A provider 5xx or connection failure exits 2.
- With `MT_TICK_SPEND_CEILING_USD` unset every row's verdict is "no ceiling configured"; with it set, rows compare correctly; with it set to `0` or negative, `Settings()` raises a validation error naming the field.
- `DbnFile.open` on each committed sample file reports `GLBX.MDP3`, the expected schema, `ESH1 → 5482` with its interval, and `iter_batches(1)` yields two batches with `count == 1`, native itemsize 48 (trades) or 80 (tbbo, mbp-1), and the first record's `price == 3720250000000`. The v2 sample decodes identically.
- The estimate core, run against a fake provider whose `timeseries` and `batch` surfaces raise on access, completes — the executable proof that the preflight issues no billable request.
- After the verification walkthrough, the Databento portal's usage page shows no batch jobs and no streaming charges for the day.

### Technical Requirements

- `uv sync` installs `databento`; `uv.lock` is committed in the same commit as `pyproject.toml`.
- Unit tests under `test/unit/data/tick/` and `test/unit/cli/commands/test_data_tick.py` (Typer `CliRunner`, fake provider) run with no network and no key. No integration or load tier work — nothing touches a database.
- `ruff` clean on touched files; `mypy` zero errors on touched files (the merge bar in `pyproject.toml`).
- Files ≤ ~300 lines; `adapter.py` and `dbn_file.py` are the only importers of `databento`; a unit test asserts that no other module under `src/manta_trading` imports it.
- Every tick comparison value (tiers, delivery modes, dataset code, batch bound, env names, exit codes) has exactly one definition.
- README: `tick` added to the `mt data …` row of the CLI map; a new `### Tick / Databento` environment table with `MT_DATABENTO_API_KEY`, `MT_TICK_SPEND_CEILING_USD`, and `MT_TICK_DB_URL` (present, no consumer until 222); a `## Futures tick data` section skeleton with the `estimate` command. `.env_sample` gains both variables under `--- Optional ---`. The data-correctness contract's I10 row (tooling consistency) gains "220 fixes the tick shape: `mt data tick` subgroup, verb vocabulary as I10", and its I9 row (loud failure) cites the preflight's refusals.
- `SOURCES.md` beside the fixtures names the upstream repository, commit, licence, and which files are CME and which are not.
- The task file records the benchmark output and the retention figure (or the fact that it is still pending the account).

### Integration Requirements

- 222 can render its CHECK constraints from `STORED_TIERS` and `DeliveryMode` with no new vocabulary.
- 223 can be written against `ITickDataProvider` alone, with a fake provider in its tests, and its ceiling guard reads `Settings.tick_spend_ceiling_usd`.
- 224 can call `open_file(...).iter_batches(TICK_DECODE_BATCH_RECORDS)` from a worker thread and receive arrays in the field layout the findings table lists.

### Verification Walkthrough

Prerequisite: the PM has created the Databento account, set its provider-side budget, and put `MT_DATABENTO_API_KEY=…` in the dev `.env`. Steps 1–4 need neither.

1. **Install and discover the group.**
   ```sh
   uv sync
   uv run mt data tick --help          # lists `estimate` only
   uv run mt data tick estimate --help # shows --symbols/--stype required, --end exclusive
   ```

2. **Unit tests, no network.**
   ```sh
   uv run --extra dev pytest test/unit/data/tick test/unit/cli/commands/test_data_tick.py -q
   ```
   Expected: all pass. Among them: the decode tests over the committed sample files, the settings tests (unset → `None`; `0` → validation error naming `tick_spend_ceiling_usd`), and the "no billable surface touched" test.

3. **Decode a real file by hand.**
   ```sh
   uv run python -c "from pathlib import Path; from manta_trading.data.tick.databento.dbn_file import DbnFile; \
     f = DbnFile.open(Path('test/fixtures/databento/test_data.trades.v3.dbn.zst')); \
     print(f.dataset, f.schema, f.mappings); print([b.count for b in f.iter_batches(1)])"
   ```
   Expected: `GLBX.MDP3 trades {'ESH1': [{'start_date': datetime.date(2020, 12, 28), 'end_date': datetime.date(2020, 12, 29), 'symbol': '5482'}]}` then `[1, 1]`.

4. **The interpreter-lock benchmark.**
   ```sh
   uv run python scripts/bench_dbn_decode.py --records 2000000 --threads 4
   ```
   Expected: a table with wall time for the per-record path and the array path at 1 and 4 threads. The array path should show a multi-thread speedup; the per-record path should not. Whatever the numbers are, they go in the task file verbatim.

5. **Refusals.**
   ```sh
   env -u MT_DATABENTO_API_KEY uv run mt data tick estimate --symbols ES.c.0 --stype continuous --start 2025-01-06 --end 2025-01-11
   ```
   Expected: exit 1; the message names `MT_DATABENTO_API_KEY`. Then, with the key present, request an end far in the future: exit 1; the message states the dataset's available end.

6. **The preflight, live, free.**
   ```sh
   uv run mt data tick estimate --symbols ES.c.0 --stype continuous --start 2025-01-06 --end 2025-01-11
   ```
   Expected: a header with the dataset's available range and the five requested days' conditions, then four rows — `trades`, `tbbo`, `mbp-1`, `definition` — each with records, size, cost, and the verdict `no ceiling configured (MT_TICK_SPEND_CEILING_USD unset)`. `tbbo` and `trades` should show the same record count and different sizes; `mbp-1` should show a much larger record count. The figures themselves are the deliverable and are pasted into the task file.

7. **The ceiling.**
   ```sh
   MT_TICK_SPEND_CEILING_USD=<a value between two of the costs from step 6> uv run mt data tick estimate … --json
   ```
   Expected: the JSON `rows[*].ceiling_verdict` is `within` for the cheaper schemas and `over` for the rest; `ceiling_usd` echoes the value.

8. **Prove nothing was bought.** Open the Databento portal's usage/billing page. Expected: no batch jobs listed and no charges for today.

## Risk Assessment

### Technical Risks

- **The batch retention figure is unverified.** The design does not depend on it (deadlines come from `ts_expiration`), but 223's cadence rule does. If the account reveals a short window, 223 fires more often; the design of 220 is unchanged.
- **The batch API is changing under us.** `list_jobs` is shrinking to three fields. The adapter uses `get_job_details` from day one; the SDK version is pinned with a floor, and a later SDK removing `list_jobs`' legacy shape costs nothing here.
- **The interpreter-lock answer is from source inspection and a small benchmark.** If 226 measures a different bottleneck on real volume (for example the `COPY` encoding rather than decode), the worker moves to a process and the protocol is untouched — that path is kept open by construction (Technical Decision 6).

### Mitigation Strategies

Each risk above already names its mitigation; none blocks this slice.

## Implementation Notes

### Development Approach

Suggested order, each step leaving the tree green:

1. Dependency and constants: add `databento`, lock, `data/tick/constants.py` with the enums, dataset code, batch bound, env names; `Settings.tick_spend_ceiling_usd` and its tests.
2. Fixtures: copy the sample files with `SOURCES.md`; `dbn_file.py` and its decode tests (header, mappings, batches, v2 compatibility, itemsizes, first-record values).
3. Protocol and adapter: `provider.py` types and `ITickDataProvider`; `adapter.py` with `from_settings`, the metadata methods, `resolve_symbols`, error mapping; unit tests with a fake `Historical`-shaped object and the recorded JSON shapes (record once the key exists; until then the tests use the SDK's documented return types and are marked to be re-run against the recordings).
4. Delivery methods on the adapter (`fetch_range`, `submit_batch`, `batch_job`, `download_batch`) — thin wrappers with tests that assert the exact SDK parameters passed (encoding `dbn`, compression `zstd`, `split_duration=day`, `stype_out=instrument_id`) and that `batch_job` reads `get_job_details`.
5. Estimate core and CLI: `estimate.py`, `tick.py`, `tick_render.py`, registration in `data.py`, `CliRunner` tests including the three exit codes and the billable-surface guard.
6. Benchmark script; run it; record the table.
7. Documentation and the contract rows; the recording script; live walkthrough once the key exists.

Commit at least once per step (the project's checkpoint-per-section reading of "commit once per task").

### Special Considerations

- **No billable request, structurally.** `ESTIMATE_SCHEMAS` and the adapter's metadata methods are the whole surface the preflight touches; the fake-provider test that raises on `timeseries`/`batch` access enforces it. The recording script imports the same constant. A review of this slice should be able to confirm the property by reading two files.
- **Secrets.** The key enters only through `Settings.databento_api_key`; the adapter never logs it (the SDK itself logs only the gateway). Tests never load `.env`; they use `Settings(_env_file=None)` and `monkeypatch` as `test_settings.py` does.
- **The stale tick leftovers stay.** `data/base/tick_schema.py` (`TickEventType`) and `test/integration/test_tick_schema_integration.py` are 222's to remove, per the plan; this slice does not touch them and does not set `MT_TICK_DB_URL` anywhere, so the stale test stays skipped.
- **Thread-safety scope.** Only the CLI runs in this slice, single-threaded. The design's thread decision binds 224; the state review it requires (python rules) is recorded in Technical Decision 6 and repeated in 224's design when the worker is built.
