---
docType: review
layer: project
reviewType: arch
slice: futures-realtime-data-databento-live
targetKind: arch
rulesSource: project
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/architecture/280-arch.futures-realtime-data-databento-live.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261015
dateUpdated: 20261015
reviewedSha: d5ef8b7e26b27e29484e11de1eea0ad5994493dc
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 1
turns: 2
promptTokens: 84745
cachedTokens: 9503
cacheWriteTokens: 75238
completionTokens: 7052
durationSeconds: 63.0
costUsd: 0.373174
costBasis: provider
squadronVersion: 0.22.0
findings:
  - id: F001
    severity: concern
    category: completeness
    summary: "Segment crash recovery and durability are unspecified"
    location: "project-documents/user/architecture/280-arch.futures-realtime-data-databento-live.md#architectural-principles"
  - id: F002
    severity: concern
    category: consistency
    summary: "Supersession and reconciliation are defined on different time grains"
    location: "project-documents/user/architecture/280-arch.futures-realtime-data-databento-live.md#architectural-principles"
  - id: F003
    severity: concern
    category: completeness
    summary: "Partial-range supersession is deferred, but it is the failure path"
    location: "project-documents/user/architecture/280-arch.futures-realtime-data-databento-live.md#technical-considerations"
  - id: F004
    severity: concern
    category: feasibility
    summary: "The stand-in gateway tests the SDK against itself, and every high-risk behavior sits in the cutover slice"
    location: "project-documents/user/architecture/280-arch.futures-realtime-data-databento-live.md#technical-considerations"
  - id: F005
    severity: concern
    category: consistency
    summary: "Anticipated slices contradict each other on the `statistics` dependency"
    location: "project-documents/user/architecture/280-arch.futures-realtime-data-databento-live.md#anticipated-slices"
  - id: F006
    severity: concern
    category: completeness
    summary: "Gap lifecycle when replay succeeds is unclear, and silent in-connection loss has no detector for hours"
    location: "project-documents/user/architecture/280-arch.futures-realtime-data-databento-live.md#architectural-principles"
  - id: F007
    severity: concern
    category: technology
    summary: "The SDK reconnect must be disabled and the replacement supervisor is not designed"
    location: "project-documents/user/architecture/280-arch.futures-realtime-data-databento-live.md#architectural-principles"
  - id: F008
    severity: concern
    category: abstraction
    summary: "Live projection reusing 220's worker is asserted, not shown, and races reconciliation"
    location: "project-documents/user/architecture/280-arch.futures-realtime-data-databento-live.md#envisioned-state"
  - id: F009
    severity: concern
    category: antipattern
    summary: "On-request `ohlcv-1s` adds a spend-incurring request path that the document's own numbers do not justify"
    location: "project-documents/user/architecture/280-arch.futures-realtime-data-databento-live.md#technical-considerations"
  - id: F010
    severity: concern
    category: completeness
    summary: "Cost of the subscription-month history is not in the assumptions"
    location: "project-documents/user/architecture/280-arch.futures-realtime-data-databento-live.md#technical-considerations"
  - id: F011
    severity: concern
    category: completeness
    summary: "Deferring service placement and the execution-feed decision leaves the capture contract open"
    location: "project-documents/user/architecture/280-arch.futures-realtime-data-databento-live.md#technical-considerations"
  - id: F012
    severity: note
    category: extension-points
    summary: "Single-host single-session capture has no redundancy"
    location: "project-documents/user/architecture/280-arch.futures-realtime-data-databento-live.md#architectural-principles"
  - id: F013
    severity: note
    category: dependencies
    summary: "Dependency list includes 180 for a deferred item only"
    location: "project-documents/user/architecture/280-arch.futures-realtime-data-databento-live.md"
---

# Review: arch — initiative 280

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Segment crash recovery and durability are unspecified

The "receive path never waits on the database" principle makes a local DBN file the only durable copy until reconciliation, which can be about 8 hours later or more. The document does not say the following:
- **When the file is fsynced.** Nothing states a sync policy or what is acceptable to lose.
- **When the manifest row is written.** Checksum and record count exist "once closed", so the open-segment state is ambiguous.
- **What happens after a crash.** An OOM kill, `kill -9` or power loss leaves a truncated DBN with a torn final record and no closed manifest row.
- **How recovery works.** It is unclear how the next start derives "last record received" for replay, or how the lost tail is recorded as a gap. Replay-from-last-record is only as good as that value.

A crash is the most common cause of a gap, so this is load-bearing. The capture slice should not be left to invent it. A synchronous write inside the SDK callback also needs a stated model: a blocking disk write there causes the slow-reader condition the design wants to avoid.

### [CONCERN] Supersession and reconciliation are defined on different time grains

Segments rotate at the UTC day boundary. Supersession is "per whole UTC day, in one transaction". Reconciliation compares "per instrument and session", using the CME session model, whose session runs 18:00 to 17:00 ET and straddles UTC midnight. A session is therefore split across two supersession units. A mismatch found in a session cannot be mapped cleanly onto the unit that gets superseded. The design also does not say whether history for a day can be "taken" when only half of the session has become available. Pick one grain. Normally that is the session or trade date, or the document must say how the UTC day maps onto sessions.

### [CONCERN] Partial-range supersession is deferred, but it is the failure path

The document says "the design must define the outcome when history can't be taken for a day". That is the case where a gap can never be repaired: the gap is older than the 24-hour replay window and history cannot be fetched. The outcomes are a permanent gap, a degraded live day kept, or partial-range supersession, and the choice changes the manifest schema and the supersession transaction. Leaving it to "the reconciliation design decides whether that case exists" defers a schema-affecting decision to the fourth slice, after capture and projection are built. The same applies to the plan's included 12 months of `tbbo`: nothing says what happens to unreconcilable days once that window or the subscription lapses.

### [CONCERN] The stand-in gateway tests the SDK against itself, and every high-risk behavior sits in the cutover slice

The stand-in is "written from the SDK's own protocol code and exercised by the SDK's own client". It can only prove the capture path agrees with the SDK. It cannot validate the behaviors that decide whether the design works: replay semantics and boundary duplicates, `REPLAY_DATA_AGED_OUT`, `parent` symbology, mid-session `SymbolMappingMsg` and definition ordering, gateway behavior in the 17:00 pause, and slow-reader handling. The assumptions list admits this, yet all of these are deferred to the cutover day. If the `parent` symbology assumption fails, the live adapter, the mid-segment identifier handling and the projection all need rework after the other slices are "done". Two things would reduce this:
- A short, bounded first subscription month, or a smoke test with a minimal session, before the stand-in's fault-injection suite is built.
- A decision on the fallback (explicit raw symbols resolved from definitions) before the live adapter slice, since it changes the adapter.

The stand-in with fault injection is also a substantial component in its own right. Its scope should be capped to what can be validated against real behavior.

### [CONCERN] Anticipated slices contradict each other on the `statistics` dependency

The capture service subscribes live to `statistics` and `definition` (Scope, Envisioned State). Yet "Bar and statistics history" is declared "independent of the live slices". The Current State section says `TickSchema` knows only `trades`, `tbbo`, `mbp-1` and `definition`, so `statistics` "cannot even be archived today". Capture and projection therefore need the `statistics` archive schema and table before the history slice exists, or live `statistics` capture silently moves out of scope. The document does not say which. The live-statistics storage and the segment delivery mode for non-tick schemas are not described at all. The sizing table also covers only `tbbo`, so `statistics` and `definition` live volume is unmeasured.

### [CONCERN] Gap lifecycle when replay succeeds is unclear, and silent in-connection loss has no detector for hours

The Envisioned State says that on disconnect the service "reconnects with replay... and records the interval between as a gap with its cause". If replay fills the interval, it is not a gap. The document never states when a gap is opened, closed or marked filled. The `filled` provenance state in Reads implies a lifecycle that is never described. Separately, the stated detection design relies on connection signals only, so loss inside a healthy connection is invisible until reconciliation. That is after the roughly 8 hour history lag, and it needs history to be priced at $0 or paid for. The "No silent holes" goal is really "no silent holes after 8 hours or more". Say so, or add a cheap in-stream sanity check, such as per-instrument rate against expectation within an open session.

### [CONCERN] The SDK reconnect must be disabled and the replacement supervisor is not designed

The document notes that the SDK reconnect resubscribes without `start`, so it would silently create holes. It never states that the SDK reconnect must be turned off and that the service owns the whole reconnect loop. That loop needs backoff, auth failure versus transient failure handling, a cap on replay attempts, and behavior on `REPLAY_DATA_AGED_OUT` (fall back to a gap plus history fill). Nothing about the sync, thread or async model of the Live client against the file writer is specified either. Under CLAUDE.md the failure handling must be explicit, not defaulted. The trim rule for replay overlap, "the capture slice's to specify", is also load-bearing. Event-time replay filtering with ties on `ts_event`, and the natural key `(instrument, event time, sequence)` where sequence is not dense, is exactly where duplicates or drops get introduced.

### [CONCERN] Live projection reusing 220's worker is asserted, not shown, and races reconciliation

220's worker and ingest ledger are unit-oriented (record count, first and last time, volume per closed unit). "One code path" is claimed for projecting open, still-growing segments. That needs incremental offsets or checkpoints, partial ledger rows and idempotent re-projection, and none of it is described. Reconciliation supersedes a day in one transaction while projection may still be writing that day's trailing segment. The date-boundary segment is the obvious case, because the lag gives only a few hours of margin. Projection also has no stated behavior when a tick references an instrument with no stored definition ("a definition must be stored before ticks that depend on it are projected"). It must block, quarantine or error. CLAUDE.md forbids silent fallbacks, so the document should choose one.

### [CONCERN] On-request `ohlcv-1s` adds a spend-incurring request path that the document's own numbers do not justify

The whole 16 years of `ohlcv-1s` for both products is 17 GB billable, and the document itself says "storage is not the argument". The remaining argument is that no consumer exists yet. A request-driven fetch-and-keep path means an API or operator call can trigger a paid Databento acquisition. It needs authorization, spend-guard interaction, long-running job handling behind a request/response API, and concurrent-request deduplication. That is more machinery than a one-time bulk pull, and it conflicts with the "resist complexity" principle. The document also notes the data is only available while subscribed. On-request therefore risks never fetching what cannot be fetched later. Either pull it whole at cutover (a configuration of the same acquisition path) or defer the schema entirely. The same slice also bundles `ohlcv-1m` and `statistics` history under a live-data initiative with no dependency on the live work. It could be a separate initiative.

### [CONCERN] Cost of the subscription-month history is not in the assumptions

The assumptions list covers `tbbo` pricing at $0, but not whether 16 years of `ohlcv-1m`, `ohlcv-1s` and `statistics` are included in Standard or billed. The document gives billable sizes (1.4 GB, 1.6 GB, 17 GB) but no dollar figure. The cutover script runs these pulls on cutover day. Add the pricing check to the first-day checklist, and have the script fail on a non-zero estimate rather than rely on the spend guard alone.

### [CONCERN] Deferring service placement and the execution-feed decision leaves the capture contract open

The document itself notes that placing capture inside `manta-acquisition.slice` risks an OOM kill of the group stopping capture, and that capture's own memory need is small. The decision is easy and the answer is obviously "its own slice or unit", so it should be made here rather than at slice design. The execution-feed question is more genuinely open, but "must not foreclose either option" is not turned into a constraint. State what it implies, for example that capture exposes an in-process event hook after the durable write, or that nothing stops a later fan-out stage.

### [NOTE] Single-host single-session capture has no redundancy

The Standard plan allows 10 sessions per dataset, but the design uses one with no standby. Host restarts and deploys therefore always create gaps. The 24-hour replay window and the daily history bridge are the intended recovery, and that is reasonable for a research store. It is worth stating explicitly that planned deploys should be scheduled in the CME pause, so they produce a recorded, replayable segment end rather than a gap.

### [NOTE] Dependency list includes 180 for a deferred item only

The frontmatter lists `180-arch.data-serving.md` as a dependency, but the only link is the deferred WebSocket item. No slice here builds on 180 unless the consumer decision picks the stream endpoint. Mark it conditional so the dependency graph does not imply a hard ordering.

### Run Digest

- Response length: 12473 chars
- Response is newline-free: no
- Tool calls made: 1
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 2
- Tokens — prompt / cached / cache write / completion / reasoning: 84745 / 9503 / 75238 / 7052 / not computed
- Duration: 63.0 s
- Cost: $0.37 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 13
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 13
- Finding-shaped matches — surviving validation: 13
