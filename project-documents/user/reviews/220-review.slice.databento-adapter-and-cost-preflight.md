---
docType: review
layer: project
reviewType: slice
slice: databento-adapter-and-cost-preflight
targetKind: slice
rulesSource: project
project: trading-data
verdict: FAIL
verdictSource: stated
sourceDocument: project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md
aiModel: claude-opus-5-5
status: complete
dateCreated: 20260926
dateUpdated: 20260926
reviewedSha: 4403e3509ccad414850db76030dad3abcaf5102e
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 9
findings:
  - id: F001
    severity: fail
    category: error-handling
    summary: "Submitting a batch job treats a timeout as safe to retry, which risks buying the same range twice"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:164-166"
  - id: F002
    severity: concern
    category: error-handling
    summary: "The new I/O paths don't say what happens on a hang, a timeout or a mid-transfer disconnect"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:140"
  - id: F003
    severity: concern
    category: dependency-direction
    summary: "A Databento-specific file type (`DbnFile`) leaks through the provider-neutral protocol"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:190"
  - id: F004
    severity: concern
    category: interface-design
    summary: "Free and paid operations share one interface, and the \"no billable request\" test is aimed at the wrong object"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:233"
  - id: F005
    severity: concern
    category: integration
    summary: "The preflight's ceiling answer is not the check 223's guard will make"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:156"
  - id: F006
    severity: concern
    category: data-model
    summary: "`RecordBatch.tier` can't describe a batch from a definition file"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:193"
  - id: F007
    severity: concern
    category: nfr
    summary: "The stated batch memory budget is broken by the design's own definition-schema figure"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:160"
  - id: F008
    severity: concern
    category: code-structure
    summary: "Two sets of shared values have no single stated home"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:55"
  - id: F009
    severity: concern
    category: testing
    summary: "Interim invented metadata fixtures contradict the \"real or recorded\" rule"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:324"
  - id: F010
    severity: concern
    category: nfr
    summary: "The architecture's ingest-throughput target isn't restated here"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:158"
  - id: F011
    severity: note
    category: scope
    summary: "The retention window is carried to a task, but the check the architecture requires on it has no owner yet"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:31"
  - id: F012
    severity: note
    category: concurrency
    summary: "State the thread-safety contract on the protocol itself"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:150"
  - id: F013
    severity: pass
    category: scope
    summary: "Scope and sequencing match the slice plan"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md#technical-scope"
  - id: F014
    severity: pass
    category: architecture-alignment
    summary: "The CLI/API surface decision is made once and matches the architecture"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:152"
  - id: F015
    severity: pass
    category: architecture-alignment
    summary: "Execution model, synchronous protocol, spend-ceiling setting and import direction"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md#technical-decisions"
---

# Review: slice — slice 220

**Verdict:** FAIL
**Model:** claude-opus-5-5

## Findings

### [FAIL] Submitting a batch job treats a timeout as safe to retry, which risks buying the same range twice

Technical Decision 10 maps "`requests` connection/timeout errors → `ProviderTransientError`" for every adapter call. For the free metadata calls that is correct. For `submit_batch` it is wrong, because submitting a job spends money and repeating it spends it again.

If the server accepts `batch.submit_job` and the connection drops before the response arrives, the job exists and is billed, but the client has no job id. A "transient" error tells 223 that retrying is safe. A retry submits and pays for a second job.

The architecture says the manifest row is written "at *submitted*, the moment money is committed … so the same range is never bought twice" (arch:51). That only works if the adapter reports "outcome unknown" separately from "did not happen".

The design needs to fix three things here, because it is the slice that fixes the error taxonomy "once, at the adapter boundary":
- Submit failures need their own outcome: either a distinct `ProviderError` subclass, or a stated rule that a submit transport error is never transient.
- It must say how 223 recovers an unknown outcome, for example by listing recent jobs through `list_jobs` and matching on dataset, schema, symbols and range before it submits anything new.
- A unit test must assert that a timeout during submit does not produce `ProviderTransientError`.

### [CONCERN] The new I/O paths don't say what happens on a hang, a timeout or a mid-transfer disconnect

The slice ships seven network calls: the metadata calls, `resolve_symbols`, `fetch_range`, `submit_batch`, `batch_job`, `download_batch`, plus the recording script. None of them has a stated way of behaving when the connection hangs.
- **Hang:** there is no request timeout. The doc doesn't say whether the SDK sets one, whether the adapter passes one, or where that value is defined. An interactive `estimate` that hangs forever is a silent failure under I9 (loud failure).
- **Disconnect during `fetch_range`:** a streamed request is billed again if it is repeated (finding at line 30). The design doesn't say what happens to a half-written `dest`: delete it, leave it, or report the bytes written. It also doesn't say whether the error is marked "billed, outcome partial" so 223 doesn't retry blindly.
- **Disconnect during `download_batch`:** the SDK retries up to `BATCH_DOWNLOAD_MAX_RETRIES = 5`. The design doesn't say what the adapter returns or raises once those retries run out, or whether partial files stay in `dest_dir`. Downloads are free, so a retry is safe here, but the leftover-file behaviour decides whether 223's size and checksum check can trust what is on disk.

Add a failure-mode table (path → hang / timeout / disconnect → error class and what is left on disk). Give the request timeout one named constant in `constants.py`.

### [CONCERN] A Databento-specific file type (`DbnFile`) leaks through the provider-neutral protocol

`ITickDataProvider.open_file(path) -> DbnFile` makes `data/tick/provider.py`, the neutral protocol, depend on `data/tick/databento/dbn_file.py`, the concrete adapter. That is the wrong direction.

It also contradicts the slice's own rule that everything above the protocol "sees … frozen dataclasses, never an SDK type" (line 112). And it contradicts the architecture's rule that the adapter "is the whole acquisition-side surface a second provider would replace" (arch:61). 224 is told to read `DbnFile.mappings/partial/not_found` directly (line 216), which ties the ingest pass to the DBN header shape.

Define a protocol-level file interface in `provider.py` (for example `ITickFile`: dataset, schema, symbol mappings, `iter_batches`) and have `DbnFile` implement it. The architecture allows the storage projection to be Databento-shaped. It does not allow the acquisition protocol to be.

### [CONCERN] Free and paid operations share one interface, and the "no billable request" test is aimed at the wrong object

`ITickDataProvider` (lines 179-191) mixes free metadata calls with three paid ones: `fetch_range`, `submit_batch`, `download_batch`. `build_estimate` takes the whole interface, so the preflight holds a reference to operations that spend money. The only thing enforcing the slice's central safety property is a test.

That test is described as "a fake provider whose `timeseries` and `batch` surfaces raise on access". But `timeseries` and `batch` are attributes of the SDK's `Historical` client, not of the protocol. As worded, the test either injects a fake `Historical` into `DatabentoTickProvider`, which needs a constructor injection point the design never specifies, or it uses a protocol fake that has no such attributes, and then the test proves nothing.

The architecture treats billable requests outside the estimate → guard → manifest path as defects (arch:53). The cleaner fix is to split the interface: `ITickMetadataProvider` (free) for `build_estimate`, and a separate purchasing interface for 223. The "no billable request" property then holds by type and the test becomes a backstop. At minimum:
- Say which object the test fakes.
- Specify the adapter's injection point for the client (for example `DatabentoTickProvider(client: Historical)` alongside `from_settings`).

### [CONCERN] The preflight's ceiling answer is not the check 223's guard will make

The architecture defines the ceiling as "the most one acquisition pass may commit, summed over the units it would submit" (arch:53). 223 buys a tier together with its `definition` units (plan entry 223). The preflight compares each schema's cost with the ceiling on its own (lines 128 and 202). So `tbbo` can show `within` while `tbbo` plus `definition` is `over`.

That contradicts the design's claim that "the operator sees the comparison 223's guard will make" (line 156). Either report the verdict per purchasable bundle (each tier plus `definition`), or relabel the column as a per-schema comparison and drop the claim.

### [CONCERN] `RecordBatch.tier` can't describe a batch from a definition file

`RecordBatch` is typed `tier: TickTier`, but Technical Decision 4 states that `definition` "is not a tier". Yet 224 decodes definition units through the same iterator ("projects definition units first", plan entry 224), and `DbnFile` is tested on the definition sample (line 168).

A definition batch has no valid `tier` value. Either:
- type the field as a schema enum that includes `definition` (with `TickTier` as a subset), or
- add a separate discriminator for companion schemas.

Otherwise 224 has to work around the type.

### [CONCERN] The stated batch memory budget is broken by the design's own definition-schema figure

Technical Decision 7 states a budget of "one in-flight batch ≤ 32 MiB per worker". The same paragraph computes a `definition` batch at 130 MiB, about four times over, and dismisses it with "definitions are small in count".

That depends on how the request is scoped, not on the bound itself. A parent-symbol request (`stype_in=parent`) can return every contract and spread under the product, and the design never checks how many that is. The architecture requires the bound to be "sized so a decoded batch stays inside a stated memory budget" (arch:61). Either:
- make the batch bound depend on record size (for example, bytes budget ÷ itemsize, with one constant for the budget), or
- state the maximum definition count the claim depends on and add a check that fails loudly when it is exceeded.

### [CONCERN] Two sets of shared values have no single stated home

- **Exit codes:** line 55 names `constants.py` as their home ("exit codes' home"). Lines 103, 166 and 202 say they are "defined once in `tick.py`". The requirement at line 242 ("exactly one definition") can't be checked while the doc names two places.
- **Symbology values:** `stype_in` (four values, used by CLI choices and requests) and `stype_out` (`instrument_id`, passed to `submit_batch`) are comparison values, but the constants module doesn't list them. `resolve_symbols(..., stype_out: str)` takes a bare string (line 185). Add a symbology enum to `constants.py` and use it in `TickRequest`, the CLI and the adapter.

### [CONCERN] Interim invented metadata fixtures contradict the "real or recorded" rule

Technical Decision 11 says fixtures are "real or recorded, never invented". The architecture's testing strategy says the same ("a fixture in an invented format is a false pass", arch:151). Development step 3 then lets the adapter and renderer tests run on shapes taken from the SDK documentation "until then", marked for a later re-run.

The success criteria never require the recordings to exist before the slice closes. The only related check (line 245) accepts the retention figure as "still pending". Make committing the recorded JSON fixtures, and re-running the tests against them, a completion requirement. Alternatively, state explicitly that the slice cannot close until the PM has created the account.

### [CONCERN] The architecture's ingest-throughput target isn't restated here

This slice fixes the decode path that 224 and 226 build on (the execution model, `iter_batches`, the batch bound). The architecture attaches a pass/fail target to exactly that path: "a day's sessions for the configured universe must ingest well inside the daily pass interval" (arch:135).

The slice restates the memory budget but not this target. Its benchmark (Technical Decision 12, walkthrough step 4) reports speedup only, with no link to the target. Restate the target and say what the benchmark can and cannot show about it, for example "decode rate on synthetic repeats is an upper bound only; 226 decides".

### [NOTE] The retention window is carried to a task, but the check the architecture requires on it has no owner yet

The architecture requires slice design to verify that the retention window "covers several consecutive missed firings" (arch:123). Deriving deadlines from `ts_expiration` is the right structural answer, and the risk section (line 308) acknowledges the gap.

Say explicitly that 223's design owns the check "window ≥ N missed firings", so it isn't lost when the recorded figure arrives.

### [NOTE] State the thread-safety contract on the protocol itself

Technical Decision 2 has async callers wrap each call in `asyncio.to_thread`. That pattern will easily call one provider instance from several default-executor threads. Technical Decision 6 says "the SDK client is not shared across workers". Put that rule in the `ITickDataProvider` docstring ("an instance is not thread-safe; one per concurrent caller") so 223 doesn't have to find it in a decision paragraph.

### [PASS] Scope and sequencing match the slice plan

The architecture's first sketched slice also included the session model and the contract amendment's frame. The plan splits those into 221, and that split is recorded as a deliberate difference. The out-of-scope list lines up with 221–230. The rule "a slice never writes to a table a later slice creates" (arch:159) is kept: this slice adds no tables, no migrations and no database access. The architecture's verification items (embargo, record-count query, how symbol mappings appear in files, whether the decoder releases the interpreter lock, delivery modes) are each resolved with a cited source, or marked unresolved and carried into a task.

### [PASS] The CLI/API surface decision is made once and matches the architecture

Technical Decision 3 makes the single joint decision the architecture requires (arch:139): an `mt data tick` subgroup and a `/api/v1/futures/*` namespace. It justifies the choice from futures identity and the Kalshi precedent. Its verb list, with `pass` standing in for `daemon`, matches the architecture's own wording of I10 ("daemon-equivalent pass", arch:47).

### [PASS] Execution model, synchronous protocol, spend-ceiling setting and import direction

- **Execution model:** a synchronous protocol with bounded-batch iteration off the event loop is what arch:61 requires. The threads decision rests on inspecting the SDK source, and the path to switching to processes stays open.
- **Spend ceiling:** `MT_TICK_SPEND_CEILING_USD` as `Decimal | None` with `gt=0` matches "no default; absent means refuse to purchase" (arch:53).
- **API key:** it enters only through `Settings`, and the SDK's fallback to its own environment variable is explicitly never used.
- **Imports:** the Kalshi code is used as a pattern only, and nothing imports from `data/kalshi`, as the architecture's rule against one data source importing another requires (arch:73). A unit test restricts `databento` imports to the two adapter modules.

### Run Digest

- Response length: 15668 chars
- Response is newline-free: no
- Tool calls made: 9
- Tool calls failed: 0
- Stop reason: end_turn
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 15
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 15
- Finding-shaped matches — surviving validation: 15
