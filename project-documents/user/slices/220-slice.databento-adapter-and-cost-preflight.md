---
docType: slice-design
slice: databento-adapter-and-cost-preflight
project: trading
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [902]
interfaces: [222, 223, 224, 226, 229, 230]
effort: 3
dateCreated: 20260925
dateUpdated: 20260926
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
| Availability edge | `get_dataset_range(dataset)` returns the available range; `get_dataset_condition(dataset, start_date, end_date)` returns one dict per date (date, condition, last modified) and, unlike the cost, size, and record-count endpoints, takes an **inclusive** `end_date`. Databento's historical API serves data "older than 24 hours"; a request past the edge is rejected with the available end in the message. | SDK docstrings; databento.com/historical |
| Delivery modes | `timeseries.get_range(..., path=)` streams a request to a file. `batch.submit_job(dataset, symbols, schema, start, end, encoding, compression, split_symbols, split_duration ∈ {day, week, month, year, none}, split_size, delivery='download', stype_in, stype_out, limit)`. Databento's guidance: streaming for small on-demand work, batch for "larger data requests, typically over 5 GB"; a repeated stream request is billed again, a batch file can be re-downloaded without charge inside its window. **Per-mode size limits are unresolved**: none appears in the SDK or the readable blog posts, but a published limit would be on the unreadable docs pages, so the absence is not verification. Recorded from the account with the retention figure; 223's batch-versus-direct choice depends on it. | `batch.py`, `timeseries.py`; Databento blog "streaming vs. batch download" |
| Batch retention window | The window exists: job states are `queued`, `processing`, `done`, `expired`, and the job record carries `ts_expiration`. **The published length is unresolved** (docs unreadable here). Design consequence: the manifest deadline is always the job's own `ts_expiration`, never a constant; the published figure is recorded as a task once the PM's account exists. | `batch.py` |
| Batch API change in flight | `batch.list_jobs` is being reduced to `id`, `state`, `ts_received`; full details move to `batch.get_job_details(job_id)`, present in SDK 0.87.0. The adapter uses `get_job_details` from the start. | Databento blog "Upcoming changes to the historical batch API" (2026-08) |
| Symbology | `stype_in` ∈ {`raw_symbol`, `instrument_id`, `parent`, `continuous`}; `stype_out` defaults to `instrument_id`. `symbology.resolve(dataset, symbols, stype_in, stype_out, start_date, end_date)` is free. | `symbology.py` |
| Mapping records in delivered files | Historical files carry no `SymbolMappingMsg` records — that type is documented as "a symbol mapping message from the live API". Mappings live in the DBN **metadata header**: `mappings` (input symbol → list of `{start_date, end_date, symbol}` intervals, where `symbol` is the instrument id as a string when `stype_out=instrument_id`), plus `partial` and `not_found` symbol lists. Confirmed on the sample files: `{'ESH1': [{2020-12-28, 2020-12-29, '5482'}]}`. The Python `Metadata` object exposes `mappings` as a dict; the Rust-side `symbol_map_for_date` helper is not exposed in Python. | `rust/dbn/src/record.rs`, `metadata.rs`; local decode |
| Record sizes | `TradeMsg` (schema `trades`) is 48 bytes; `Mbp1Msg` (schemas `tbbo` and `mbp-1`) is 80 bytes; `InstrumentDefMsg` v3 is 520 bytes. Fields as delivered: `ts_event`, `ts_recv`, `rtype`, `publisher_id`, `instrument_id`, `price` (fixed-point, 1e-9), `size`, `action`, `side`, `flags`, `depth`, `ts_in_delta`, `sequence`; tbbo adds `bid_px_00`, `ask_px_00`, `bid_sz_00`, `ask_sz_00`, `bid_ct_00`, `ask_ct_00`. | `size_hint` and `to_ndarray` dtype on the sample files |
| Bounded batch decode | `DBNStore.to_ndarray(count=N)` yields NumPy structured arrays of at most N records, one record per row at the native record size, over a zstd stream reader. This is the bounded-batch iterator the architecture asks for, provided by the SDK. | `databento/common/dbnstore.py`; local decode |
| Decoder and the interpreter lock | `DBNDecoder.decode()` and `write_and_decode()` in `python/src/dbn_decoder.rs` take the `Python` token for their whole body and never call `allow_threads`/`detach`: the per-record decoder **holds the GIL** on CPython 3.12. (`#[pymodule(gil_used = false)]` only matters on free-threaded builds.) The `to_ndarray` path does not use that decoder: it decompresses through `python-zstandard`, which releases the GIL during decompression (documented as an implementation detail), and views the bytes with `numpy.frombuffer`. | `dbn` v0.70.0 source; python-zstandard docs |
| Real sample files | `databento/dbn` `tests/data/test_data.{trades,tbbo,mbp-1}.v3.dbn.zst` are real `GLBX.MDP3` records: symbol `ESH1`, instrument id 5482, two records each from 2020-12-28, prices `3720250000000` (= 3720.25). `test_data.definition.v3.dbn.zst` is a real `definition` record but from `XNAS.ITCH` (MSFT) — valid for decode-shape tests, not a CME definition. v1/v2 variants exist for DBN-version compatibility. Apache-2.0. | local decode |
| HTTP timeouts and partial files | Metadata, symbology, job-detail, submit, and stream requests all go through `BentoHttpAPI._get/_post/_stream` with `timeout=(100, 100)` — a fixed class attribute, not a public parameter. `_stream` checks the HTTP status before opening `path`, opens it with mode `x+b` (fails if it exists), and on a mid-stream error raises `BentoError("Error streaming response")` leaving the partial file. The batch download (`_download_batch_file`) issues `requests.get` with **no timeout**, resumes a partial file with a `Range` header, retries 5 times, and on a SHA-256 mismatch only **logs a warning** — the file is returned as good. | `databento/common/http.py`, `historical/api/batch.py` |
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
- New package `src/manta_trading/data/tick/`: the tick-provider protocols (free metadata, acquisition, file reading) and their request/result types, the tick constants module (schema, symbology, and delivery-mode enums, tier sets, dataset code, decode byte budget, download timeout, env-name constants), and the preflight core.
- New subpackage `src/manta_trading/data/tick/databento/`: the adapter (the metadata and acquisition protocols over the SDK's `Historical` client, error mapping, a verified batch-file download of its own) and the DBN file reader (the file protocol: header mappings, bounded batches).
- One new class in `providers/errors.py`: `ProviderOutcomeUnknownError` (Technical Decision 10).
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
- **PM action, before fixture recording and the verification walkthrough:** create the Databento account, set the provider-side account budget (the architecture's second spend guard), and put `MT_DATABENTO_API_KEY` in the dev `.env`. Nothing needs the key to build or to pass the decode and settings tests; recording the metadata fixtures, re-pointing the metadata-shaped tests at them, and running `estimate` live do — so the account is a prerequisite for *closing* this slice (Technical Decision 11), not for starting it.
- Network access to `hist.databento.com` for the recording script and the walkthrough only.

### Interfaces Required

- `manta_trading.providers.errors` — `ProviderError`, `ProviderAuthError`, `ProviderTransientError`, `ProviderPermanentError`; reused, and extended by one sibling class (Technical Decision 10).
- `manta_trading.cli.output` — `print_result`, `print_error`, `make_table`; the `--json` convention every verb follows.
- `manta_trading.config.Settings` — loaded once in `cli/app.py`'s callback and read from `ctx.obj["settings"]`, as `mt data kalshi` does.
- `data/kalshi/constants.py` and `cli/commands/kalshi.py` as the pattern for a per-domain constants module and a source subgroup with integer exit codes defined once.

## Architecture

### Component Structure

```
src/manta_trading/data/tick/
  __init__.py
  constants.py            # TickSchema, TICK_TIERS, STORED_TIERS, COMPANION_SCHEMAS,
                          # ESTIMATE_SCHEMAS, SType, DeliveryMode, CME_DATASET,
                          # TICK_DECODE_BATCH_BYTES, TICK_DOWNLOAD_TIMEOUT_SECONDS,
                          # env-name constants — every tick comparison value, once
  provider.py             # ITickMetadataProvider (free), ITickAcquisitionProvider
                          # (paid + delivery), ITickFileReader, ITickFile (Protocols)
                          # + frozen dataclasses: TickRequest, DatasetRange,
                          # DayCondition, SymbolResolution, SymbolInterval,
                          # BatchJob, RecordBatch
  estimate.py             # build_estimate(metadata: ITickMetadataProvider, request,
                          # ceiling) -> EstimateReport; the CLI renders it
  databento/
    __init__.py
    adapter.py            # DatabentoTickProvider(client, download_http): metadata +
                          # acquisition protocols; from_settings(); error mapping;
                          # verified batch-file download
    dbn_file.py           # DbnFileReader / DbnFile: the file protocols over DBNStore —
                          # header + iter_batches() -> RecordBatch

src/manta_trading/cli/commands/tick.py          # tick_app; `estimate`; exit codes (once, as kalshi.py)
src/manta_trading/cli/commands/tick_render.py   # Rich table for the estimate report

test/fixtures/databento/*.dbn.zst               # real GLBX.MDP3 sample files (+ SOURCES.md)
test/fixtures/databento/metadata/*.json         # recorded free-endpoint responses
scripts/record_databento_fixtures.py            # free calls only; needs the key
scripts/bench_dbn_decode.py                     # per-record vs array path, 1 vs N threads
```

`adapter.py` and `dbn_file.py` are the only modules that import `databento`; `dbn_file.py` is the only one that reads a DBN byte. Everything above the protocols — the estimate core, the CLI, and every later pass — sees the `I*` protocols, `TickRequest`, `RecordBatch`, and the other frozen dataclasses, never an SDK type and never `DbnFile` by name. A second provider replaces `databento/` and nothing else.

### Data Flow

Preflight (this slice's only runtime path):

```
mt data tick estimate --symbols … --stype … --start … --end … [--json]
  → Settings (key, ceiling) → DatabentoTickProvider.from_settings
  → build_estimate(metadata, TickRequest, ceiling):   # typed ITickMetadataProvider
      dataset_range()                          # once; refuse if end > edge, naming the edge
      dataset_condition(start, end)            # once; tally available/pending/degraded/missing
      for schema in ESTIMATE_SCHEMAS:          # trades, tbbo, mbp-1, definition
          record_count(request@schema)
          billable_size(request@schema)
          cost(request@schema)
      for tier in STORED_TIERS:                # bundle for this request shape:
          bundle = cost(tier) + cost(definition)
          compare bundle with the ceiling      # set: within/over; unset: "no ceiling configured"
      mbp-1                                    # verdict: "not purchasable in this initiative"
  → EstimateReport → tick_render (Rich) or print_result(to_dict(), json_mode=True)
```

Decode (exercised by tests and the benchmark here; by 224 in production):

```
archive file → ITickFileReader.open_file(path) → ITickFile
  → header: dataset, schema, stype_in, start, end, mappings, partial, not_found
  → iter_batches() → RecordBatch(schema, records: ndarray, count)   # ≤ TICK_DECODE_BATCH_BYTES each
```

Every metadata call is one SDK call, made synchronously, on the calling thread. The preflight is a plain Typer command with no event loop; it needs none.

### State Management

None persisted. The preflight holds one `EstimateReport` in memory and prints it. `DbnFile` owns its file handle and zstd reader for the life of one iteration; nothing is shared between iterations or threads. `DbnFileReader` holds no client, no key, and no state, so a decode worker needs no provider instance at all. The setting is read once per process.

Thread-safety contract, stated in the docstrings of `ITickMetadataProvider` and `ITickAcquisitionProvider` themselves: an instance is not thread-safe — one instance per concurrent caller. An async caller using `asyncio.to_thread` from several tasks constructs one per task (or per worker), never shares one. `ITickFileReader` is stateless and may be shared; each `ITickFile` it returns belongs to one thread.

## Technical Decisions

1. **The `databento` SDK, not a hand-rolled httpx client.** The SDK provides the DBN decoder, the zstd stream handling, the array-batch iterator, batch-download with bounded retries (`BATCH_DOWNLOAD_MAX_RETRIES = 5`), and the symbology helpers — all of which this initiative needs and none of which the project should re-implement. Cost: a heavier install (pyarrow, pandas, requests, aiohttp alongside the project's httpx). Accepted; the footprint is recorded in the findings table. The SDK's environment-variable fallback (`DATABENTO_API_KEY`) is never relied on — the key always comes from `Settings` (python rules: one value, one source).

2. **A synchronous protocol; callers move it off the loop.** The SDK is blocking (`requests`), and the architecture fixes the iterator as synchronous. So every tick protocol is synchronous throughout — metadata, submit, poll, download, decode — and an async caller (the 223/224 passes, which follow the Kalshi async phase contract) wraps each call in `asyncio.to_thread`, the project's existing pattern for the Kalshi signer, the JSONL sink, and the orchestrator's chunk writer. Rejected alternative: an `async def` protocol that hides `to_thread` inside the adapter. That makes every method look cheap on the loop while blocking underneath, contradicting the python rules' "<1 ms of synchronous work inside `async def`" and putting the executor decision in the wrong layer.

3. **CLI: an `mt data tick` subgroup. API: a `/api/v1/futures/*` namespace. One decision, made here for the whole initiative.** Futures identity (contract, product, roll method) has no analogue in the granularity-switch surface: `data status` takes `--daily/--minute` booleans, `data get` and `data pull` take positional tokens from `constants.Granularity` (`1m`, `1d`), and none of those can carry "which contract under which rule". Kalshi took a subgroup for the same reason. The data-correctness contract's tooling-consistency invariant (I10, "every granularity supports the same operator-facing surface") is *written* in subgroup form — `mt data tick {status, ingest, daemon, coverage, backfill}` — so the subgroup is its literal shape, and the requirement it actually imposes is the verb vocabulary, which the subgroup adopts: `status`, `coverage`, `pass` (the daemon-equivalent, in the Kalshi shape), `ingest`, `backfill`, `debug`, plus `estimate` (this slice) and `get` (229). The API mirrors the choice after 188's Kalshi precedent: `/api/v1/futures/*` with contract and roll-method parameters, rather than tick as a granularity on `/api/v1/bars/{symbol}`. Composed surfaces (`mt data overview`, `health`, `accounting`, `/api/v1/status`, `/api/v1/overview`) gain tick lines in place under either choice and are unaffected. 229 and 230 apply this decision; they do not revisit it.

4. **The schema vocabulary lands here, not in 222; tiers are a subset of it.** The plan's 222 entry says the storage track "adds the tier enum"; the preflight needs the vocabulary now, and a value used in a conditional is defined once. One enum, `TickSchema(StrEnum)` = `TRADES="trades"`, `TBBO="tbbo"`, `MBP_1="mbp-1"`, `DEFINITION="definition"` — the provider's own schema names, so a schema is also a request parameter. Named subsets, never re-spelled values: `TICK_TIERS = (TRADES, TBBO, MBP_1)` (the candidate tiers the preflight compares), `STORED_TIERS = frozenset({TRADES, TBBO})` (what this initiative stores; `mbp-1` is estimate-only), `COMPANION_SCHEMAS = frozenset({DEFINITION})` (bought with a tier, never a tier itself), and `ESTIMATE_SCHEMAS = (*TICK_TIERS, DEFINITION)`, the one tuple the preflight iterates. 222 renders its tier CHECK constraints from `STORED_TIERS` and any schema column from `TickSchema`. Because `TickRequest.schema`, `RecordBatch.schema`, and `ITickFile.schema` are `TickSchema`, a definition file's batches are typed like any other — 224 decodes definition units through the same iterator with no special case. Symbology gets the same treatment: `SType(StrEnum)` = `RAW_SYMBOL`, `INSTRUMENT_ID`, `PARENT`, `CONTINUOUS`, used by `TickRequest.stype_in`, the CLI's `--stype` choices, and the adapter; `stype_out` is always `SType.INSTRUMENT_ID` (Technical Decision 9).

5. **`MT_TICK_SPEND_CEILING_USD` is `Decimal | None = None`, validated `> 0` when set.** "No default" means no default *value*: unset is the state "no ceiling configured", which 223 treats as "refuse to purchase", not as a number. Every `Settings` field today is optional in this way and `Settings()` is built for every `mt` command, so a pydantic-required field would break `mt --help`. Money is `Decimal` for the same reason Kalshi parses fixed-point strings into `Decimal` — exactness — and the SDK's float cost is converted at the adapter boundary. The env name is spelled once: `TICK_SPEND_CEILING_ENV = "MT_TICK_SPEND_CEILING_USD"` in `data/tick/constants.py`, cited by the renderer and tests. The architecture defines the ceiling over everything a pass would submit, and 223 buys a tier together with its `definition` units, so the preflight's verdict is on the **bundle** — each stored tier's cost plus the `definition` cost for the same request — never on a schema alone. It is an estimate *for this request shape*: the `definition` part is priced with the request's own symbols and `stype_in`, while the architecture scopes definitions per configured product, so 223's design owns the definition scope and its guard may price that part differently. The `definition` row shows its own figures and `bought with each tier` in the verdict column. The `mbp-1` row shows its figures and `not purchasable in this initiative` — it is outside `STORED_TIERS` and exists only to price the alternative.

6. **Execution model: worker threads over the array-batch path; the per-record decoder is not used for ingest.** The findings settle the architecture's open question on two facts. The SDK's per-record decoder holds the GIL, so threads running it would be no faster than the loop. The SDK's `to_ndarray(count=N)` path holds the GIL only for the `frombuffer` view; decompression runs with the lock released. It also produces one contiguous array per batch at the native record size instead of N Python objects, which is what makes a stated memory budget meaningful. Therefore: `RecordBatch.records` is a NumPy structured array in the provider's own field layout, `DbnFile.iter_batches` is `to_ndarray(count=…)` behind the protocol with the count derived from the byte budget (Technical Decision 7), and the worker that decodes a unit is a **thread**. The process fallback the architecture describes is kept available by construction — the iterator is a synchronous generator with no shared state, so it runs unchanged in a thread or a subprocess — and is adopted only if 226 measures otherwise. The benchmark in this slice confirms the direction on sample data; 226 measures it on purchased volume. State review for the executor (python rules): `DbnFile` owns its handle and reader; provider instances are not shared across workers — the contract is written on the protocols themselves (State Management).

7. **The batch bound is a byte budget, so it holds for every schema.** One constant, `TICK_DECODE_BATCH_BYTES = 32 * 1024 * 1024` in `data/tick/constants.py`: one in-flight decoded batch per worker. `DbnFile.iter_batches()` takes no count; it computes `records_per_batch = TICK_DECODE_BATCH_BYTES // record_size`, where `record_size` is the native size of the file's record type as decoded (from the SDK's record class for the file's schema, after its DBN-version upgrade). At the verified sizes that is 699,050 `trades` records, 419,430 `tbbo`/`mbp-1` records, and 64,527 `definition` records — each batch ≤ 32 MiB regardless of how many contracts a `parent` request expands to. A unit test pins the three record sizes on the sample files and asserts `records.nbytes ≤ TICK_DECODE_BATCH_BYTES` for every batch. 226 replaces the budget from measurements and rewrites the docstring; nothing else changes.

8. **The request grain is calendar-day UTC with an exclusive end, matching the provider.** `TickRequest(dataset, symbols, stype_in, schema, start: date, end: date)`; `end` is exclusive because `get_record_count`/`get_cost`/`submit_job` treat it so, and mismatching the provider's own convention is how a day goes missing (memory: FastAPI's date/datetime union dropped a day the same way). The CLI help states it. The protocol is exclusive-end everywhere; the one provider endpoint that differs, `get_dataset_condition` (inclusive `end_date`), is converted inside `DatabentoTickProvider.dataset_condition`, which passes `end - 1 day`, and a unit test asserts the SDK receives that inclusive date. A `TickRequest` is immutable; `with_schema()` returns the same request at another schema, which is how the preflight guarantees "the same instrument and range" across tiers.

9. **Both delivery modes land a file, and the vocabulary that names them lives with the adapter.** `DeliveryMode(StrEnum)` = `BATCH_JOB="batch_job"`, `DIRECT_RANGE="direct_range"`. `fetch_range(request, dest)` streams a direct request to `<dest>.partial` via `get_range(path=)` and renames it to `dest` only when the stream completes without error, so the name rule below holds for both delivery modes; `submit_batch(request)` → `BatchJob` read back through `get_job_details`; `batch_jobs_since(since)` → `list_jobs(since=)` then `get_job_details` per id, for reconciling an unknown submit outcome (Technical Decision 10); `download_batch(job_id, dest_dir)` → verified files. Every request is sent with `stype_out=SType.INSTRUMENT_ID`, so header mappings always resolve to instrument ids, which the adapter converts to `int` in `SymbolInterval`. 222's manifest discriminator consumes the enum; the realtime initiative adds its own member later. The batch job's `ts_expiration` is the only source of a unit's download deadline.

   **The batch download is the adapter's own, not the SDK's `batch.download`**, because the SDK's has two defects the findings table records: no request timeout (a stalled transfer hangs a worker forever) and a checksum mismatch that is only a warning (a corrupt file is returned as good — a silent failure under I9). The adapter reads `batch.list_files(job_id)` (URL, size, SHA-256 per file) and fetches each file with the injected `httpx.Client` (the project's HTTP client; basic auth with the key; `timeout=TICK_DOWNLOAD_TIMEOUT_SECONDS`). It writes to `<name>.partial` and renames to `<name>` only after the size and SHA-256 match. Before fetching, it compares an existing `.partial` with the size `list_files` reports: **equal** (a crash after the last byte, before the rename) → no request, straight to hash and rename; **smaller** → resume with `Range: bytes=N-`; **larger** → delete and restart. A `200` answer to a ranged request means the server ignored the range, so the file is truncated and the body written from the start. A `416` means the `.partial` and the server disagree, so it is deleted → `ProviderTransientError`, never Permanent. So a file under its final name is always complete as far as the adapter can check, and a `.partial` never is — the rule 223's verify step relies on. There is no retry loop inside the adapter: a failure leaves the `.partial` for the next call to resume, and 223's reconcile phase is the retry.

   What "complete" means differs by mode. A batch file is checked against the provider's SHA-256. A direct-range file has no provider checksum; the adapter's guarantee is only that the stream ended without error. Content verification of a direct-range unit (for example, decoded record count equal to the free `get_record_count` for the same request) is 223's design, which owns the architecture's *downloaded → verified* transition for every unit.

10. **Errors are mapped once, at the adapter boundary. A paid call is never "transient".** Free calls (metadata, symbology, job details, job listing, file listing, download): `BentoServerError`, HTTP 429, and connection/timeout errors → `ProviderTransientError`; `BentoClientError` 401/403 → `ProviderAuthError`; any other `BentoClientError` or a malformed response → `ProviderPermanentError`. Paid calls (`submit_batch`, `fetch_range`) have exactly three outcomes: success; a 4xx answer, meaning the provider refused and charged nothing → 429 is `ProviderTransientError` (rate-limited before any charge — the one case where retrying a paid call is safe), 401/403 `ProviderAuthError`, any other 4xx `ProviderPermanentError`; **anything else** — a timeout, a dropped connection, a 5xx (a gateway can fail after the backend accepted), a mid-stream error — → `ProviderOutcomeUnknownError`. It is a new sibling of `ProviderTransientError` under `ProviderError` in `providers/errors.py`, deliberately **not** a subclass of it, so no handler that retries transients can catch it by accident. Its docstring says: the request may have been accepted and charged; reconcile before anything is resubmitted. A missing key at `from_settings` → `ProviderAuthError` naming `MT_DATABENTO_API_KEY`.

   How an unknown outcome is reconciled is 223's design; this slice gives it the means. For `submit_batch`, `batch_jobs_since(t)` returns every job since `t` with its `TickRequest` (dataset, schema, symbols, `stype_in`, start, end), so 223 can find the job it may have created and adopt its id before submitting anything new. `fetch_range` leaves no provider-side record to reconcile against; the adapter deletes `<dest>.partial` before raising, and 223's design decides how an unknowable charge is counted against the ceiling. A process kill mid-stream raises nothing and leaves `<dest>.partial` behind; that is crash residue, not a caller bug. A direct request cannot resume, so the next `fetch_range` deletes a leftover `.partial` before streaming — and 223, which calls it only for a unit its manifest shows as unfinished, applies the same unknown-charge policy to that unit.

   Failure modes, per path:

   | Path | Cost | Timeout or hang | Disconnect mid-transfer | HTTP 4xx | HTTP 5xx | Left on disk |
   |---|---|---|---|---|---|---|
   | metadata, `resolve_symbols`, `batch_job`, `batch_jobs_since` | free | SDK's fixed 100 s connect/read → Transient | → Transient | 401/403 Auth, else Permanent | Transient | nothing |
   | `submit_batch` | paid | SDK's 100 s → **OutcomeUnknown** | → **OutcomeUnknown** | 429 Transient; 401/403 Auth; else Permanent | **OutcomeUnknown** | nothing |
   | `fetch_range` | paid | SDK's 100 s → **OutcomeUnknown** | → **OutcomeUnknown** | 429 Transient; 401/403 Auth; else Permanent (the SDK checks status before opening the file) | **OutcomeUnknown** | nothing — `<dest>.partial` deleted on error; a leftover `.partial` from a kill is deleted before the next stream; an existing final `dest` is refused up front (`FileExistsError` — a finished unit is never re-bought) |
   | `download_batch` | free | `TICK_DOWNLOAD_TIMEOUT_SECONDS` → Transient | → Transient | 429 Transient; 401/403 Auth; 404/410 (expired) Permanent; 416 → `.partial` deleted, Transient | Transient | `.partial` kept for resume (equal size → hash only; larger → restart); final name only when verified; a checksum mismatch deletes the `.partial` → Transient (re-download is free) |

   The SDK's 100 s timeout is not a public parameter, so this slice does not restate it as a constant of its own; `TICK_DOWNLOAD_TIMEOUT_SECONDS` is the one timeout the project controls. No retry loop of our own around metadata calls: the preflight is interactive and a transient failure is reported with exit code 2 (provider). Exit codes follow Kalshi's pattern — integers defined once, in `cli/commands/tick.py`, as `kalshi.py` defines its own. A unit test per paid method asserts that a timeout, a connection error, and a 5xx each raise `ProviderOutcomeUnknownError` and never `ProviderTransientError`.

11. **Fixtures are real or recorded, never invented — and the slice does not close on anything else.** Decode tests run over the `databento/dbn` sample files (v3, plus one v2 to prove the reader does not assume the current DBN version), copied with a `SOURCES.md` naming the repository, commit, and Apache-2.0 licence. Metadata-response fixtures are recorded by `scripts/record_databento_fixtures.py` from the free endpoints only (the script imports `ESTIMATE_SCHEMAS` and the metadata method names and touches nothing else) and committed as JSON; the adapter, the renderer, and the estimate core are tested on those shapes. Until the recordings exist, adapter tests may be written against the SDK's documented return types, but the slice is **not complete** until the recordings are committed and every metadata-shaped test runs on them (Success Criteria). The PM's account is therefore a completion prerequisite, not only a walkthrough one. The `XNAS.ITCH` definition sample is used for record-shape tests only and is labelled as non-CME in `SOURCES.md`.

12. **The benchmark input is real records, and is not a correctness fixture.** The sample files hold two records each — enough to prove decode, useless for throughput. `bench_dbn_decode.py` builds its input in memory by writing a real header followed by the real trade records repeated to a stated count, decodes it through both paths in 1 and N threads, and prints wall time, records per second, and speedup. Its output goes in the task file as the empirical answer beside the source-inspection answer; it is never used to assert parsing. The architecture's pass/fail target sits on this path: *a day's sessions for the configured universe must ingest well inside the daily pass interval*. The benchmark cannot decide it. It measures decode only, on repeated records, with no `COPY` and no real session volume, so its rate is an upper bound on ingest throughput, and it can only fail the target (a decode rate already too slow), never pass it. 226 decides the target on purchased data.

## Implementation Details

### API Contracts

**Protocols** (`data/tick/provider.py`), synchronous throughout. Free and paid operations are separate interfaces, so "the preflight issues no billable request" holds by type — `build_estimate` accepts an `ITickMetadataProvider` and has no reference through which to spend:

```python
class ITickMetadataProvider(Protocol):      # free; not thread-safe, one per caller
    def dataset_range(self, dataset: str) -> DatasetRange: ...
    def dataset_condition(self, dataset: str, start: date, end: date) -> tuple[DayCondition, ...]: ...
    def record_count(self, request: TickRequest) -> int: ...
    def billable_size(self, request: TickRequest) -> int: ...          # bytes, uncompressed
    def cost(self, request: TickRequest) -> Decimal: ...               # USD
    def resolve_symbols(self, request: TickRequest) -> SymbolResolution: ...

class ITickAcquisitionProvider(Protocol):   # 223 only; not thread-safe, one per caller
    def fetch_range(self, request: TickRequest, dest: Path) -> Path: ...          # PAID
    def submit_batch(self, request: TickRequest) -> BatchJob: ...                 # PAID
    def batch_job(self, job_id: str) -> BatchJob: ...                             # get_job_details
    def batch_jobs_since(self, since: datetime) -> tuple[BatchJob, ...]: ...      # reconcile
    def download_batch(self, job_id: str, dest_dir: Path) -> tuple[Path, ...]: ...  # verified

class ITickFileReader(Protocol):            # stateless; no client, no key
    def open_file(self, path: Path) -> ITickFile: ...

class ITickFile(Protocol):                  # one thread; owns its handle
    dataset: str
    schema: TickSchema
    stype_in: SType
    start: datetime
    end: datetime
    mappings: Mapping[str, tuple[SymbolInterval, ...]]   # input symbol → instrument-id intervals
    partial: tuple[str, ...]
    not_found: tuple[str, ...]
    def iter_batches(self) -> Iterator[RecordBatch]: ...   # each ≤ TICK_DECODE_BATCH_BYTES
```

`DatabentoTickProvider` implements the first two; its constructor is the injection point, `DatabentoTickProvider(client: databento.Historical, download_http: httpx.Client)`, and `from_settings(settings)` builds both from `Settings.databento_api_key`. `DatabentoTickProvider` is a context manager and owns the `httpx.Client` it holds: `__exit__` closes it, whether `from_settings` built it or a test injected it. Callers use `with DatabentoTickProvider.from_settings(settings) as provider:` — the CLI for one command, 223 for one worker's life. `Historical` needs no close (it opens a `requests` call per request). `DbnFileReader` implements `ITickFileReader`, and the `DbnFile` it returns implements `ITickFile` — no module outside `databento/` names either class. `SymbolInterval` is frozen: `start_date: date`, `end_date: date`, `instrument_id: int`. `RecordBatch` is frozen: `schema: TickSchema`, `records: numpy.ndarray` (structured, provider field names — the storage projection may be provider-shaped; the protocol types are not), `count: int`. `BatchJob` carries `job_id`, `request: TickRequest`, `state`, `ts_received`, `ts_expiration`, `record_count`, `billed_size`, `actual_size`, `package_size`, `cost_usd` — the fields 222's manifest row is written from, plus the request 223 matches on when it reconciles.

**CLI verb:**

```
mt data tick estimate --symbols <csv> --stype {raw_symbol|instrument_id|parent|continuous}
                      --start YYYY-MM-DD --end YYYY-MM-DD [--json]
```

`--symbols` and `--stype` are both required — a cost tool that guesses the symbol set is a cost tool that is wrong by an order of magnitude, silently. `--end` is exclusive (stated in help); `--stype` choices come from `SType`. Output, per schema in `ESTIMATE_SCHEMAS`: records, billable bytes (and a human-readable size), and cost in USD. Each stored-tier row adds the bundle cost (tier + `definition`) and the ceiling verdict on that bundle (`within`, `over`, or `no ceiling configured (MT_TICK_SPEND_CEILING_USD unset)`); the `definition` row shows `bought with each tier` and the `mbp-1` row `not purchasable in this initiative` in that column. Above the table: dataset available range, the requested range, and the day-condition tally. Exit codes, defined once in `tick.py`: `0` ok; `1` preflight (missing key, invalid arguments the command itself checks — `end ≤ start` — and a requested end past the available edge, whose message names the edge); `2` provider error. Click's own usage errors (a missing required option, a `--stype` outside the choices) also exit `2`, before the command body runs; this collision is inherited from `kalshi.py` and is stated in the help epilog rather than worked around. `--json` emits `EstimateReport.to_dict()`.

**Settings:**

```python
tick_spend_ceiling_usd: Decimal | None = Field(default=None, gt=0)   # MT_TICK_SPEND_CEILING_USD
```

## Integration Points

### Provides to Other Slices

- **222 (storage track):** `TickSchema`, `STORED_TIERS`, `DeliveryMode`, and the `BatchJob` field set the manifest row is modelled on.
- **223 (acquisition pass):** `ITickMetadataProvider` and `ITickAcquisitionProvider`; `ProviderOutcomeUnknownError` and `batch_jobs_since` (223's design owns the reconcile policy for an unknown submit outcome and the charge policy for an unknown `fetch_range` outcome); the file-name rule for both delivery modes (final name ⇒ complete — checksum-verified for batch files, stream-completed for direct-range files — and `.partial` ⇒ not), with content verification of direct-range units left to 223; `Settings.tick_spend_ceiling_usd`; the exclusive-end request grain; the rule that a unit's deadline is `BatchJob.ts_expiration`. **223's design also owns the architecture's retention check** — that the recorded retention window covers several consecutive missed firings at 223's cadence (architecture, "Delivery mode and retention"). This slice only records the figure.
- **224 (ingest pass):** `ITickFileReader`, `ITickFile.mappings/partial/not_found`, `iter_batches`, `RecordBatch`, `TICK_DECODE_BATCH_BYTES`, and the thread-worker decision. A decode worker needs only a `DbnFileReader`, never a provider instance or the key.
- **226 (proof):** the byte budget and its docstring to rewrite; the benchmark script to re-run on purchased data; the ingest-throughput pass/fail target (Technical Decision 12); the mapping-completeness check is defined against `ITickFile.mappings` (every `instrument_id` in a file has an interval covering its date, else the raw-symbol fallback — `stype_in=raw_symbol`, already a request parameter — is adopted).
- **229 / 230:** the subgroup and namespace decision (Technical Decision 3) and the verb vocabulary.

### Consumes from Other Slices

- The provider registry (902) as it stands. If the `databento` profile's `api_key_env` ever changes, `from_settings` reads `Settings.databento_api_key` and would need the same change — the name is spelled in `profiles.py` and `config/__init__.py` today, and this slice adds no third copy.
- `cli/output.py` helpers; a change to the `--json` convention there applies here automatically.

## Success Criteria

### Functional Requirements

- `mt data tick estimate` with a valid key prints one row per schema in `ESTIMATE_SCHEMAS` with records, billable size, and cost, plus the bundle cost and ceiling verdict on each stored-tier row, and exits 0.
- With `MT_DATABENTO_API_KEY` unset it exits 1 and the message names the variable. With `end ≤ start` it exits 1. With `--end` past the dataset's available end it exits 1 and the message states the available end. A provider 5xx or connection failure exits 2.
- `dataset_condition` for a request `[start, end)` passes the SDK `end_date = end − 1 day` and returns exactly one condition per requested day.
- With `MT_TICK_SPEND_CEILING_USD` unset every stored tier's verdict is "no ceiling configured" and `mbp-1`'s is "not purchasable in this initiative" whatever the ceiling; with it set, each tier's verdict compares its bundle (tier + `definition`) correctly, including a case where the tier alone is within and the bundle is over; with it set to `0` or negative, `Settings()` raises a validation error naming the field.
- `DbnFileReader().open_file` on each committed sample file reports `GLBX.MDP3`, the expected schema, `ESH1 → SymbolInterval(2020-12-28, 2020-12-29, 5482)`, and `iter_batches()` yields the two records at native itemsize 48 (trades) or 80 (tbbo, mbp-1) with the first record's `price == 3720250000000`. The v2 sample decodes identically. The definition sample decodes with `schema == TickSchema.DEFINITION` and itemsize 520. Every batch satisfies `records.nbytes ≤ TICK_DECODE_BATCH_BYTES` (tested with a patched small budget so a file spans several batches).
- `build_estimate`, run end to end over a real `DatabentoTickProvider` built with an injected fake `Historical` whose `metadata`/`symbology` return the recorded fixtures and whose `timeseries` and `batch` attributes raise on access, completes — the executable backstop to the type-level separation. mypy rejects passing anything that is not an `ITickMetadataProvider`.
- Each paid method (`submit_batch`, `fetch_range`), given a fake client that raises a timeout, a connection error, or a 5xx, raises `ProviderOutcomeUnknownError` and never `ProviderTransientError`; a 429 raises `ProviderTransientError`; any other 4xx raises `ProviderPermanentError`/`ProviderAuthError`. `fetch_range` leaves neither `dest` nor `<dest>.partial` after any failure, deletes a leftover `<dest>.partial` before streaming, and refuses an existing final `dest`.
- `download_batch`, against a fake HTTP transport: a full transfer with a matching SHA-256 leaves only the final name; an interrupted transfer leaves `<name>.partial` and the next call resumes it with a `Range` header; a `.partial` already at full size is hashed and renamed with no request sent; a `.partial` larger than expected is deleted and restarted; a `200` answer to a ranged request restarts the file from byte 0; a `416` deletes the `.partial` and raises `ProviderTransientError`; a checksum mismatch deletes the `.partial` and raises `ProviderTransientError`; a stalled response raises `ProviderTransientError` after `TICK_DOWNLOAD_TIMEOUT_SECONDS`.
- `DatabentoTickProvider` used as a context manager closes its `httpx.Client` on exit, including on an exception.
- The recorded metadata fixtures are committed and every adapter, estimate, and renderer test that consumes a metadata shape runs on them. No test in the slice depends on a hand-written metadata response.
- After the verification walkthrough, the Databento portal's usage page shows no batch jobs and no streaming charges for the day.

### Technical Requirements

- `uv sync` installs `databento`; `uv.lock` is committed in the same commit as `pyproject.toml`.
- Unit tests under `test/unit/data/tick/` and `test/unit/cli/commands/test_data_tick.py` (Typer `CliRunner`, fake provider) run with no network and no key. No integration or load tier work — nothing touches a database.
- `ruff` clean on touched files; `mypy` zero errors on touched files (the merge bar in `pyproject.toml`).
- Files ≤ ~300 lines; `adapter.py` and `dbn_file.py` are the only importers of `databento`; a unit test asserts that no other module under `src/manta_trading` imports it.
- Every tick comparison value has exactly one definition: schemas and tier sets, symbology types, delivery modes, dataset code, decode byte budget, download timeout, and env names in `data/tick/constants.py`; exit codes in `cli/commands/tick.py`.
- README: `tick` added to the `mt data …` row of the CLI map; a new `### Tick / Databento` environment table with `MT_DATABENTO_API_KEY`, `MT_TICK_SPEND_CEILING_USD`, and `MT_TICK_DB_URL` (present, no consumer until 222); a `## Futures tick data` section skeleton with the `estimate` command. `.env_sample` gains both variables under `--- Optional ---`. The data-correctness contract's I10 row (tooling consistency) gains "220 fixes the tick shape: `mt data tick` subgroup, verb vocabulary as I10", and its I9 row (loud failure) cites the preflight's refusals.
- `SOURCES.md` beside the fixtures names the upstream repository, commit, licence, and which files are CME and which are not.
- The task file records the benchmark output, and the batch retention figure and any per-mode (batch, direct) size limits as read from the account.

### Integration Requirements

- 222 can render its CHECK constraints from `STORED_TIERS`, `TickSchema`, and `DeliveryMode` with no new vocabulary.
- 223 can be written against `ITickMetadataProvider` and `ITickAcquisitionProvider` alone, with fakes in its tests; its ceiling guard reads `Settings.tick_spend_ceiling_usd`; it can catch `ProviderOutcomeUnknownError` separately from `ProviderTransientError`.
- 224 can call `reader.open_file(path).iter_batches()` from a worker thread, holding no provider instance, and receive arrays in the field layout the findings table lists.

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
   uv run python -c "from pathlib import Path; from manta_trading.data.tick.databento.dbn_file import DbnFileReader; \
     f = DbnFileReader().open_file(Path('test/fixtures/databento/test_data.trades.v3.dbn.zst')); \
     print(f.dataset, f.schema, dict(f.mappings)); print([b.count for b in f.iter_batches()])"
   ```
   Expected: `GLBX.MDP3 trades {'ESH1': (SymbolInterval(start_date=datetime.date(2020, 12, 28), end_date=datetime.date(2020, 12, 29), instrument_id=5482),)}` then `[2]`.

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
   Expected: a header with the dataset's available range and the five requested days' conditions, then four rows — `trades`, `tbbo`, `mbp-1`, `definition` — each with records, size, and cost; the `trades` and `tbbo` rows add a bundle cost (tier + `definition`) and the verdict `no ceiling configured (MT_TICK_SPEND_CEILING_USD unset)`; `mbp-1` shows `not purchasable in this initiative`. `tbbo` and `trades` should show the same record count and different sizes; `mbp-1` should show a much larger record count. The figures themselves are the deliverable and are pasted into the task file.

7. **The ceiling.**
   ```sh
   MT_TICK_SPEND_CEILING_USD=<a value between two of the bundle costs from step 6> uv run mt data tick estimate … --json
   ```
   Expected: the JSON tier rows' `ceiling_verdict` is `within` for the cheaper bundles and `over` for the rest, judged on `bundle_cost_usd`; `ceiling_usd` echoes the value.

8. **Prove nothing was bought.** Open the Databento portal's usage/billing page. Expected: no batch jobs listed and no charges for today.

## Risk Assessment

### Technical Risks

- **The batch retention figure is unverified.** The design does not depend on it (deadlines come from `ts_expiration`), but 223's cadence rule does. If the account reveals a short window, 223 fires more often; the design of 220 is unchanged.
- **The batch API is changing under us.** `list_jobs` is shrinking to three fields. The adapter uses `get_job_details` from day one; the SDK version is pinned with a floor, and a later SDK removing `list_jobs`' legacy shape costs nothing here.
- **The batch download is ours, not the SDK's.** It exists because the SDK's has no timeout and treats a checksum mismatch as a warning. The cost is one small module-level concern in `adapter.py` (list files, ranged fetch, hash, rename), tested against a fake transport. If a later SDK fixes both defects, the adapter can switch back without changing the protocol.
- **The interpreter-lock answer is from source inspection and a small benchmark.** If 226 measures a different bottleneck on real volume (for example the `COPY` encoding rather than decode), the worker moves to a process and the protocol is untouched — that path is kept open by construction (Technical Decision 6).

### Mitigation Strategies

Each risk above already names its mitigation; none blocks this slice.

## Implementation Notes

### Development Approach

Suggested order, each step leaving the tree green:

1. Dependency and constants: add `databento`, lock, `data/tick/constants.py` with the enums, tier sets, dataset code, byte budget, download timeout, env names; `ProviderOutcomeUnknownError`; `Settings.tick_spend_ceiling_usd` and its tests.
2. Fixtures and file protocols: copy the sample files with `SOURCES.md`; `ITickFileReader`/`ITickFile` in `provider.py`; `dbn_file.py` and its decode tests (header, mappings as `SymbolInterval`, byte-bounded batches, v2 compatibility, itemsizes, first-record values, definition schema).
3. Metadata protocol and adapter: `ITickMetadataProvider` and the request/result types; `adapter.py` with the injected constructor, `from_settings`, the metadata methods, `resolve_symbols`, free-call error mapping; unit tests with a fake `Historical`-shaped object. Until the recordings exist, the tests use the SDK's documented return types; step 7 swaps them for the recordings, and the slice does not close before it does.
4. Acquisition protocol on the adapter: `fetch_range`, `submit_batch`, `batch_job`, `batch_jobs_since` as thin wrappers with tests that assert the exact SDK parameters passed (encoding `dbn`, compression `zstd`, `split_duration=day`, `stype_out=instrument_id`) and that job reads use `get_job_details`; the paid-call error mapping and its outcome-unknown tests; the verified download and its fake-transport tests.
5. Estimate core and CLI: `estimate.py` (bundle verdicts), `tick.py`, `tick_render.py`, registration in `data.py`, `CliRunner` tests including the three exit codes and the billable-surface backstop.
6. Benchmark script; run it; record the table with the throughput caveat (Technical Decision 12).
7. Once the key exists: the recording script, the committed recordings, and every metadata-shaped test re-pointed at them; the batch retention figure from the account; documentation and the contract rows; the live walkthrough.

Commit at least once per step (the project's checkpoint-per-section reading of "commit once per task").

### Special Considerations

- **No billable request, structurally.** `build_estimate` takes an `ITickMetadataProvider`, which has no paid method; mypy enforces the type, and the adapter-level test with `timeseries`/`batch` raising on access is the backstop. The recording script imports `ESTIMATE_SCHEMAS` and calls only the metadata protocol. A review of this slice should be able to confirm the property by reading `provider.py` and `estimate.py`.
- **Secrets.** The key enters only through `Settings.databento_api_key`; the adapter never logs it (the SDK itself logs only the gateway). Tests never load `.env`; they use `Settings(_env_file=None)` and `monkeypatch` as `test_settings.py` does.
- **The stale tick leftovers stay.** `data/base/tick_schema.py` (`TickEventType`) and `test/integration/test_tick_schema_integration.py` are 222's to remove, per the plan; this slice does not touch them and does not set `MT_TICK_DB_URL` anywhere, so the stale test stays skipped.
- **Thread-safety scope.** Only the CLI runs in this slice, single-threaded. The design's thread decision binds 224; the state review it requires (python rules) is recorded in Technical Decision 6 and repeated in 224's design when the worker is built.
