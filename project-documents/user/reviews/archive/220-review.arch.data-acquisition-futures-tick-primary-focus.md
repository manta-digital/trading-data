---
docType: review
layer: project
reviewType: arch
slice: data-acquisition-futures-tick-primary-focus
targetKind: arch
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md
aiModel: moonshotai/kimi-k3
status: complete
dateCreated: 20260921
dateUpdated: 20260921
reviewedSha: ae171807c2f9486c76badc025c8e049417ff2147
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 33
findings:
  - id: F001
    severity: concern
    category: completeness
    summary: "Acquisition pass has no durable checkpoint across submit → download; a mid-pass death can force a repurchase"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Envisioned State"
  - id: F002
    severity: concern
    category: consistency
    summary: "The completeness definition requires per-instrument-session record counts the manifest cannot hold as described"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Envisioned State"
  - id: F003
    severity: concern
    category: completeness
    summary: "\"Caught up\" is undecidable without a provider availability edge, and no mechanism for computing that edge is identified"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Envisioned State"
  - id: F004
    severity: concern
    category: consistency
    summary: "The isolation goal and the serving-layer failure posture contradict each other; startup-fail vs per-route degradation is undecided"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Technical Considerations"
  - id: F005
    severity: concern
    category: technology
    summary: "Blocking DBN decode and the provider's sync client are never reconciled with the async pass contract being duplicated"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Architectural Principles"
  - id: F006
    severity: concern
    category: feasibility
    summary: "Local roll-method reproducibility rests on provider symbol-mapping behavior the document flags as unverified"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Envisioned State"
  - id: F007
    severity: concern
    category: completeness
    summary: "The stale tick-schema integration test will break the integration tier the moment MT_TICK_DB_URL is set, and removal is never scheduled"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Current State"
  - id: F008
    severity: concern
    category: consistency
    summary: "Chunk-geometry decisions are split inconsistently: space partitioning deferred to measurement, compression segby stated as decided"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Envisioned State"
  - id: F009
    severity: concern
    category: completeness
    summary: "Migration CLI has no verified track→database routing; the tick track on a second instance assumes a mechanism that may not exist"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Anticipated Slices"
  - id: F010
    severity: note
    category: antipattern
    summary: "The parity mandate is delivered as a monolith; the architecture does not sequence which parity surfaces the proof itself depends on"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Design Goals"
---

# Review: arch — slice 0

**Verdict:** CONCERNS
**Model:** moonshotai/kimi-k3

## Findings

### [CONCERN] Acquisition pass has no durable checkpoint across submit → download; a mid-pass death can force a repurchase

The acquisition pass is described as one phase chain: "estimates cost and size for the remainder, refuses above the spend ceiling, and otherwise submits batch requests, waits for delivery, downloads the files into the archive, verifies them, and records manifest rows." Technical Considerations then states that batch output files are "retained for a limited window" and that "a download that misses the window is a repurchase." These two statements together define a failure the design never addresses: when in that chain is the manifest row written? If the pass records the unit only after download-and-verify, then a pass that dies (host restart, OOM, systemd stop) after `batch.submit_job` succeeds leaves no local record that money was spent; the next firing computes "(universe × range) minus the manifest," re-estimates, and re-submits — paying twice for the same range. The spend ceiling guards each pass individually, not cumulative duplicate spend. If instead the unit is recorded at submit time, then download-resume semantics, partial-file state, and the retention-window deadline become manifest fields the document never enumerates. The backfill case makes this worse: a multi-year ES purchase is exactly the case where "waits for delivery" can exceed any plausible bounded-pass window, and the pass form (per the 20260823 ADR, cited approvingly) has no checkpoint mechanism — the Kalshi contract it copies resumes from watermarks in `sync_state`, which has no analogue here. The architecture should state the manifest's state machine for an archive unit (submitted → delivered → downloaded → verified → ingested) and which transitions are pass-resumable, since it has already identified the retention window as the consequence of getting it wrong.

### [CONCERN] The completeness definition requires per-instrument-session record counts the manifest cannot hold as described

"An *instrument-session is complete* when the manifest shows its archive units acquired and ingested and the raw table's count for that session matches the manifest's record count." But the manifest as specified in Architectural Principles records counts per delivery artifact: "what the provider returned (job identity where one exists, files, sizes, checksums, record counts)." Batch jobs produce files "split by day" (Technical Considerations), while the document's own session model says a CME session "spans two calendar dates" and one file will typically contain many instruments. A per-file record count therefore cannot be joined to an instrument-session without decoding the file — which happens at ingest, after the completeness question is supposed to be answerable. Either the manifest needs per-(instrument, session) count rows derived during download/ingest (which the document never says), or the count-match leg of the completeness definition must be re-stated at the grain the manifest actually has. As written, the central operator question the whole design is built around — "which sessions were purchased and ingested" — has a verification leg whose inputs don't exist at the required grain.

### [CONCERN] "Caught up" is undecidable without a provider availability edge, and no mechanism for computing that edge is identified

"The *universe is caught up* when every configured instrument is complete for every session in its wanted range up to the provider's availability edge. Sessions the provider has not yet released are *pending*, never *missing*." The pending/missing distinction — which the document elsewhere calls load-bearing for the embargo-driven cadence — is only computable if the availability edge is an authoritative, queryable value. The only inputs the document names are the 24-hour embargo figure, explicitly marked "verify at slice design" in three separate places, and the session model. "24 hours behind" is not an edge: it does not say which sessions exist to be behind on (holidays, half-sessions), and Databento's actual availability is per-dataset and per-schema. If the edge is computed from a local rule (now minus embargo, intersected with the CME session calendar), then a provider-side delay longer than the embargo makes released-but-late sessions read as *missing* — the exact state the definition says must never occur — and the status surface will cry wolf on every provider delay. The document should name the provider metadata source for the edge (or state explicitly that the edge is rule-computed and accept that provider delays surface as acquisition-lagging rather than data-missing).

### [CONCERN] The isolation goal and the serving-layer failure posture contradict each other; startup-fail vs per-route degradation is undecided

The Design Goals state isolation as a hard property: "a tick pass failing or a provider outage cannot affect any other source." But the composition principle says every composing surface "fails explicitly when [`MT_TICK_DB_URL`] is absent from the environment it runs in — a silently missing tick line is the failure mode to design out." "Absent setting" and "tick database down" collapse into the same failure at the serving layer. I verified the current pattern in `api_server/app.py`: the lifespan eagerly constructs `TimescaleMinuteDataDB(conninfo)` and `TimescaleDailyDataDB(conninfo)`, each of which opens a `ConnectionPool(min_size=4)` at construction and raises on failure — i.e., today's pattern is fail-at-startup. If the tick handle follows that pattern (the document says surfaces "each gain a second pool"), then a tick-instance outage prevents `mt-serve` from starting at all, taking down bars serving for equities — a direct violation of the isolation goal, created by the parity goal. If instead tick routes degrade per-request, that is a new, divergent failure posture for this codebase and needs to be stated as a decision with its error shape (which endpoints, which status code when only the tick pool is down). The same section also misses the 187 D9 consequence: `create_app(db_url=…)` exists specifically so the load tier never reads the production URL from the environment; a second mandatory URL doubles that seam (`create_app(db_url=…, tick_db_url=…)`) and the load-tier story for tick endpoints is unaddressed.

### [CONCERN] Blocking DBN decode and the provider's sync client are never reconciled with the async pass contract being duplicated

The tick passes "follow the Kalshi contract as a *pattern*." I verified the Kalshi pass is asyncio-native (`asyncio.run(run_pass(...))` in `cli/commands/kalshi.py`, recorder calls via `asyncio.to_thread`). Databento's historical client and DBN decoder are blocking, CPU-bound (Rust-backed) calls; the document itself names "the provider's Rust-backed decoder versus per-record Python" and "batch sizing" as things to measure — but measurement is about *rate*, and the unaddressed decision is *structural*: where does a multi-GB blocking decode sit inside an async bounded pass so that (a) the event loop isn't stalled for minutes (which would freeze progress heartbeats the recorder writes via `to_thread`), (b) backpressure between decode and the `COPY` write path exists, and (c) the `TimeoutStartSec` budget isn't consumed by an unobservable blocking call. This is the classic sync/async impedance the project's own stack will impose on the first ingest slice, and the architecture — which is otherwise meticulous about naming structural decisions — never names it. A stated execution model (decode in worker processes/threads feeding bounded COPY batches, with the async pass orchestrating) belongs at this level because it constrains the adapter protocol's shape: an iterator-of-records protocol and a file-to-COPY-stream protocol are different seams for the "one provider adapter owns the wire format" principle.

### [CONCERN] Local roll-method reproducibility rests on provider symbol-mapping behavior the document flags as unverified

Two commitments interact badly. First: "Acquisition resolves product → contract *at the provider*, at request time" via provider-side continuous symbols (`.c`, `.n`, `.v`), because "acquisition therefore never depends on local roll machinery." Second: "The rule vocabulary must match the provider's continuous-symbol conventions closely enough that a request phrased in either resolves to the same contract on the same day, and the resolution must be reproducible … re-derived later from stored definitions and stored figures." The only bridge between what the provider actually did at request time and what the local roll machinery will later compute is "the symbol-mapping records that tie every tick to its real contract" in delivered files — and Technical Considerations lists "the mapping-message behaviour in delivered files" as an unverified slice-design item. If those records turn out to be incomplete (e.g., not covering every instrument per day, or arriving on a different cadence than trades), then: the delivered data cannot be attributed to contracts with certainty; the local and provider roll resolutions cannot be reconciled; and the "active contract on every surface" principle loses its ground truth for historical purchases. The document has correctly identified the verification item but under-weighted it: this is not a detail to confirm during slice design, it is a premise of the identity model, and a first-purchase gate should include a mapping-record completeness check as a named success criterion, with a stated fallback (raw-symbol requests against locally resolved contracts) if the premise fails.

### [CONCERN] The stale tick-schema integration test will break the integration tier the moment MT_TICK_DB_URL is set, and removal is never scheduled

The document cites `test/integration/test_tick_schema_integration.py` as evidence that slice 105's SQL is gone. I verified the file: every test is guarded by `skipif(not TICK_URL)`, and fixtures read `760_create_tick_events_hypertable.sql` from `database/migrations/` — a path I confirmed no longer exists. Today the skip guard hides the rot because `MT_TICK_DB_URL` is unset everywhere. But this initiative's own first slices set `MT_TICK_DB_URL` in dev and test environments (the document requires it in "every service environment file"), at which point the guard stops skipping and every test in the file fails with `FileNotFoundError` at fixture setup — a self-inflicted red tier landing exactly when tick work begins, and precisely the "silent, self-hiding" failure family the project's journal keeps naming. The architecture documents this as archaeology but the Anticipated Slices never assign its deletion to the storage-track slice (or anywhere). One line in the tick-storage slice — remove the file and its migration references — closes it; its absence is the kind of thing that surfaces as a confusing integration-tier failure mid-slice.

### [CONCERN] Chunk-geometry decisions are split inconsistently: space partitioning deferred to measurement, compression segby stated as decided

The Storage bullet says: "with chunk interval, compression layout (segment by instrument, order by event time and sequence), and any read-side aggregates all created per the journal rules. Whether space partitioning by instrument survives the chunk-count arithmetic is a measured decision, not an inherited one." TimescaleDB space partitioning and compression `segmentby` are the same class of decision — both determine how data is physically grouped by instrument, both interact with chunk counts and query planning, and the 20260719 journal entry (cited by this document) explicitly counts space partitioning as a chunk-count *multiplier* in ruling the 105 outline invalid. Deferring one to measurement while stating the other as settled is arbitrary; the minute tier's own history (the 105 outline's `segmentby=symbol` being part of what made its geometry pathological) argues the compression layout deserves the same "measured, not inherited" treatment — particularly since tick's per-instrument row counts are orders of magnitude apart (ES versus a thin deferred contract), which is exactly where a single global segby choice hurts. Relatedly, the claim that "a database rebuilt from the archive is byte-for-byte the same projection" is stronger than the design supports once a compression policy exists: a fresh rebuild holds uncompressed chunks until the policy fires, so "byte-for-byte" should be scoped to row content, not physical layout — or restated.

### [CONCERN] Migration CLI has no verified track→database routing; the tick track on a second instance assumes a mechanism that may not exist

The slice list includes "the `tick` migration track on the tick database." I verified the migration framework's shape: `TRACKS` in `market/schema/migrations/__init__.py` is a single in-code registry of three tracks, and the runner (`apply_migrations(pool, track)`) applies any track against whatever database the caller's URL points at. All three existing tracks live on the *same* database, so `mt data migrate apply --track kalshi` never needed per-track URL resolution. A tick track on a *different database instance* requires the migrate CLI to route track → URL (and, per 913, track → maintenance credential, since `timescale_maintenance_url` is a single setting). I could not verify how `migrate apply` (cli/commands/data.py:216) resolves its URL today — if it is a single `--db-url`/settings value, then the routing mechanism and a second maintenance credential (`MT_TICK_MAINTENANCE_URL` or equivalent) are net-new plumbing the architecture never names, alongside the second-pool plumbing it does name. The kalshi `db.py` preflight pattern (refuse to run with pending track migrations) also needs a tick equivalent stated, since the tick pass's run-context preflight will otherwise happily run against an unmigrated tick instance. Marked as concern rather than fail because the mechanism is small — but it belongs in the enumerated slice scope next to "the second-pool plumbing," not discovered inside it.

### [NOTE] The parity mandate is delivered as a monolith; the architecture does not sequence which parity surfaces the proof itself depends on

"Full parity of the operating surface … is a delivery of this initiative (PM direction 2026-09-21), not a follow-on" produces nine anticipated slices, each independently the size of a past initiative's slice: provider adapter, storage track, two passes plus production plumbing, the purchase proof, roll machinery, full CLI parity, full API parity, backup enrollment, expansion. The PM direction settles *whether* parity ships; it does not settle *ordering*, and the document misses an ordering distinction its own proof slice needs: the status/coverage/completeness surface is an *input* to the first-purchase proof (you cannot run the go/no-go without answering "what do I have" per session), while health, accounting, debug reads, and `mt data overview` lines are pure parity with no dependency from the proof. The slice list implicitly recognizes this ("the first cut of the tick status and coverage commands" lands in the passes slice, before the proof slice) but never states the principle, leaving "operator surface parity" as one undifferentiated slice whose scope (every minute-tier verb: `status`, coverage, `pull`/backfill, `get`, health, accounting, debug) is the most likely place for the initiative to stall. Given the PM's recorded constraint that "progress is painfully slow" (journal 20260907), an explicit minimal-parity-for-proof versus full-parity split would protect the initiative's actual goal — the measured ES dataset — from being held hostage by the debug verbs.

### Run Digest

- Response length: 17991 chars
- Response is newline-free: no
- Tool calls made: 33
- Tool calls failed: 1
- Stop reason: stop
- Reasoning characters: 3136
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
