---
docType: review
layer: project
reviewType: arch
slice: data-acquisition-futures-tick-primary-focus
targetKind: arch
rulesSource: project
project: trading-data
verdict: UNKNOWN
sourceDocument: project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md
aiModel: deepseek/deepseek-v4-pro
status: complete
dateCreated: 20260921
dateUpdated: 20260921
reviewedSha: d6322dc7d0c8daf2a6fe6466866befc0d5964efd
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 19
---

# Review: arch — slice 0

**Verdict:** UNKNOWN
**Model:** deepseek/deepseek-v4-pro

## Findings Not Parsed

**This review is degraded.** No verdict and no findings could be extracted from the model's response, so the verdict is left UNKNOWN rather than assumed.

**The model's actual response is not lost:** read the `### Raw Response` section below, which this artifact always carries when a review is degraded. Do not read this review as clean.

### Run Digest

- Response length: 12690 chars
- Response is newline-free: no
- Tool calls made: 19
- Tool calls failed: 0
- Stop reason: stop
- Reasoning characters: 7782
- `## Summary` located: no
- `## Findings` located: no
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 9
- Finding-shaped matches — in findings section: 0
- Finding-shaped matches — surviving validation: 0

### Raw Response

Now I have enough information to write my findings. Let me compile the analysis.

```markdown
## Summary

The document is thorough, unusually self-aware about its own deferred decisions, and well-grounded in the project's accumulated operational wisdom. It draws heavily on journal entries that encode hard-won lessons. However, it defers several load-bearing architectural decisions to slice design — decisions that shape slice boundaries and dependency ordering. The CME session model is identified as required early but its design is entirely unspecified. The migration CLI's inability to target a second database is named but the scope of that plumbing is underestimated. And several completeness/error-recovery paths are left implicit. The verdict is **CONCERNS** — the architecture is sound enough to proceed, but the deferrals listed below should be resolved before the first slice design begins.

## Findings

### [CONCERN] CME session model is a load-bearing architectural dependency left completely unspecified
category: completeness
location: project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Technical Considerations

The CME session model is listed as required "early" and "whether it is a table, a rule, or provider-sourced status records is a slice decision." But this model is not a detail — it is the foundation for coverage, completeness, session boundaries, the derived-bar cross-check, and the availability-edge logic. Every completeness definition in the document is expressed per-session, every status verb answers per-session, and the ingest ledger writes per *(instrument, session)*. The session model's shape determines whether "a session" is a computable boundary at all. The first slice (Databento adapter, cost preflight, and contract definitions) assigns the CME session model to itself, but without any architectural guidance on what "model" means beyond "not the equities calendar." If the model turns out to need stored holiday data from Databento's reference schemas, that's a purchase decision with cost implications. If it needs a procedural rule, that changes the ingest ledger's boundary logic. The architecture should state at minimum: what the session model's interface is, whether it requires stored data or a pure function, and whether it can be built from free provider data or requires a purchase.

### [CONCERN] Migration CLI multi-database plumbing scope is acknowledged but underestimated
category: feasibility
location: project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Technical Considerations

The document correctly identifies that today's `migrate apply` resolves one maintenance URL with all tracks targeting the same database. It says the tick track on another instance "needs track → URL routing and a second maintenance credential under 913's separation — net-new plumbing named here so it is scoped, not discovered." But the scope includes: extending the migration CLI to accept a target database selector, routing per-track migrations to the correct URL, maintaining two `schema_migrations` ledgers, provisioning a second maintenance credential (which per the 913 slice role split means a second `GRANT` artifact and a second settings key), and ensuring the cold-start path (`mt data init`) works for both databases. This isn't a small feature — it touches every migration-related code path and the entire credential-separation surface. The "Tick storage track" slice assigns this to itself alongside creating the hypertable, definitions, manifest, ingest-ledger tables, and removing the stale 105 integration test. That's a large slice with a cross-cutting infrastructure prerequisite that should arguably be its own foundation slice (9xx) before any tick storage migration is written.

### [CONCERN] Databento batch job wait time creates an implicit long-running pass with no polling/persistence design
category: completeness
location: project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md

The acquisition pass state machine is: *requested → submitted → delivered → downloaded → verified → ingested*. The description says the pass "submits batch requests, waits for delivery, downloads the files." But Databento batch jobs are asynchronous — the provider processes them and retains files for a limited window. The state `submitted` conflates two very different conditions: "job submitted, waiting for completion" (pass is blocked, possibly for hours) and "job submitted, delivery ready" (download can proceed). The document's reconciliation logic says "the next pass's first phase reconciles in-flight units against the provider's job status before it computes anything new." This implies the acquisition pass does NOT block waiting — it submits, records `submitted`, and exits. But then the "waits for delivery" step is actually performed by the *next* pass's reconcile phase. The state machine needs a `submitted` → `delivered` transition that is polled, not waited-for, and the pass timeout budget needs to account for this. The document never states how long a batch job typically takes (minutes? hours? overnight?) or what the polling interval should be. If batch jobs take hours and the pass is on a daily timer, the acquisition pass's `submitted` state is the normal resting state between submission and next-day reconciliation — which changes the "caught up" definition materially.

### [CONCERN] Session completeness definition has a boundary mismatch with calendar-day file splits
category: consistency
location: project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md

The completeness definitions state: "Provider files are split by calendar day and hold many instruments, so a unit's record count says nothing about one instrument-session." The ingest ledger writes per *(instrument, session)* and status checks "every unit covering it is complete." But CME sessions span two calendar dates (Sunday open through Monday's maintenance break, etc.). If a provider file is split by calendar day, a single session's records span two files (two units). The completeness definition for an instrument-session — "every unit covering it is complete" — correctly handles this, but the acquisition pass's unit request logic doesn't address session-crossing ranges. Does the universe request one day at a time, producing two units per session? Does it request session-aligned ranges? The document says "batch files are split by day by default" but doesn't say whether the acquisition pass requests by calendar day (matching provider file splits) or by session (matching the completeness grain). This is a reconciliation problem that surfaces at ingest time when the ledger needs to know which units cover which sessions.

### [CONCERN] Ingest pass resumability is asserted at file boundaries but the document acknowledges crossing that boundary may be necessary
category: completeness
location: project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md

Under "Ingest throughput and the write path," the document says "whether ingest must be resumable *within* a unit rather than at unit boundaries" is a measurement question. But the ingest pass description says "a partial ingest resumes at the file boundary." If a single DBN file for a liquid contract is multi-gigabyte and the pass dies mid-ingest, restarting from the file boundary re-decodes and re-inserts rows that were already committed. The idempotent write (natural key conflict-ignore) makes this safe, but the re-decode cost isn't accounted for. The document should state explicitly that file-boundary resumption is the designed behavior, that re-decoding already-inserted rows is accepted as the cost of simplicity, and that the measurement question is whether intra-file resumption is *needed*, not whether it's *supported*.

### [NOTE] Tick pass writes to production database via second connection — coupling underexplored
category: dependencies
location: project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md

The "Operational state has one home" principle places `pass_runs` on the production TimescaleDB, meaning the tick pass must open a second connection to the minute database. If the production database is unreachable, the tick pass either fails (can't record its run) or runs silently without run accounting. The document addresses the serving-layer failure posture (tick DB down → 503, not startup failure) but does not address the inverse: what happens when the production DB is down but the tick DB is up? Can the tick pass still acquire and ingest data if it can't write its `pass_runs` row? The isolation goal says "a tick pass failing or a provider outage cannot affect any other source" — but a production DB outage affecting the tick pass (preventing it from running) is the opposite coupling direction. This is likely acceptable (if production DB is down, everything is degraded) but should be stated explicitly rather than left implicit.

### [NOTE] Databento Python SDK threading model is a critical unknown for the adapter's execution model
category: technology
location: project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md

The adapter's execution model says the decoder is "blocking and CPU-bound" and the adapter exposes a synchronous iterator that "runs off the event loop (worker threads, or processes if the decoder holds the interpreter lock)." The Databento Python SDK (`databento`) uses a Rust core via PyO3. Whether it releases the GIL during decoding is a property of the SDK, not the design. If the GIL is held, `run_in_executor` with a `ThreadPoolExecutor` provides no parallelism — the GIL serializes the decoder with all other Python work. Multi-processing (`ProcessPoolExecutor`) then becomes necessary, which changes the data-passing model (batches must be pickled or shared via memory). This investigation — "does the Databento SDK release the GIL during DBN decoding?" — should be a slice-design obligation listed explicitly alongside the other "verify" items, not buried in a parenthetical in the adapter description. The entire ingest architecture's threading model depends on the answer.

### [NOTE] Proof measurements may invalidate storage decisions made in earlier slices
category: feasibility
location: project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Anticipated Slices

The sequencing places "Tick storage track" (hypertable creation, chunk interval, migration plumbing) before "First purchase and proof" (measurements that validate chunk interval, physical grouping, and compression layout). The storage slice explicitly defers "physical grouping by instrument" to the proof — but the hypertable must be created with *some* chunk interval and compression settings. If the proof measurements show the chosen interval is wrong, the hypertable needs to be re-chunked (an expensive operation, per journal 20260719's 13-hour `minute_ohlcv` repair). The document anticipates this by saying the database can be "rebuilt from the archive," but a rebuild-and-reingest cycle after the first purchase is a significant rework cost. A more conservative approach would be to create the hypertable with a deliberately conservative interval (e.g., 7-day chunks from the start, matching the minute tier's proven geometry) so the proof validates rather than potentially invalidates the schema. This is a slice-design decision but the architecture should flag the sequencing risk.

### [NOTE] The pass contract duplication from Kalshi is sound but the synchronization risk between the two copies is unaddressed
category: dependencies
location: project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md

The document explicitly adopts the Kalshi `PassPhase`/`PhaseReport`/`PassResult` contract by duplication, with the rationale that three copies is the evidence threshold for hoisting. This is defensible. However, 220's slices are anticipated to span multiple development cycles. If the Kalshi contract evolves (e.g., the 267 historical backfill added a `HistoricalPhase` to `PASS_PHASES`, and the pass contract's abort semantics or event model may change further), the tick copy won't benefit from those improvements. The architecture should at minimum note that the tick pass contract should be compared against the Kalshi contract at each 220 slice design to absorb any improvements, rather than assuming the copy made at slice-start remains adequate throughout the initiative.
```
