---
docType: review
layer: project
reviewType: slice
slice: ohlcv-1m-and-definition-history
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261101
dateUpdated: 20261101
reviewedSha: 11a8ea9feac9a9118f593dc7604361312a90a160
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 101659
cachedTokens: 11162
cacheWriteTokens: 90493
completionTokens: 4845
durationSeconds: 40.3
costUsd: 0.412296
costBasis: provider
runId: run-20261101-p4-b4d7fe01
squadronVersion: 0.24.11
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Family, ledger and supersession boundaries match the architecture"
    location: "project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md#D1 — `ohlcv-1m` is its own family, `BAR_1M`"
  - id: F002
    severity: pass
    category: error-handling
    summary: "Contract identity, definition history and failure shapes are handled"
    location: "project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md#D10 — Definition history: size and failure shape"
  - id: F003
    severity: note
    category: scope
    summary: "Completeness-surface scope is narrower than the architecture's principle, and the deferral is explained"
    location: "project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md#Technical Scope"
  - id: F004
    severity: note
    category: integration
    summary: "The architecture still points at 302 D5 for the \"no ticks wanted\" marker, which this slice defers"
    location: "project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md:77"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Interval-generic bar storage and projector are built for a slice that does not exist yet"
    location: "project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md#D3 — One bar projector class, instantiated per interval"
  - id: F006
    severity: note
    category: testing
    summary: "The derived bar fixture is a computed stand-in, flagged as not purchased"
    location: "project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md#D7 — Fixtures: DBN samples and one derived real-shape day"
  - id: F007
    severity: note
    category: scope
    summary: "Parity measurement and manifest-scale timing are deferred to named owners"
    location: "project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md#Technical Scope"
---

# Review: slice — slice 302

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Family, ledger and supersession boundaries match the architecture

The slice follows the architecture's "Generalising the archive" position.
- Bars get their own family (`BAR_1M`), their own table (`tick_ohlcv_1m`) and a per-schema projector in the registry.
- Supersession stays within a family, and the bundle rule stays a tick-tier rule.
- Bars never go into `minute_ohlcv` or `tick_trade`.
- The separate `tick_bar_ledger` is justified. Bar volume in `tick_ingest_ledger` would double-count the volume roll rule's input.
- Dependency directions are correct: 302 consumes 300 and 301 and provides to 303 through 307.

### [PASS] Contract identity, definition history and failure shapes are handled

The architecture requires definitions back to 2010 and asks slice design to size the storage. D10 sizes it by distinct instruments and defers the measured row count to 304.

Failure modes for the new ingest path are named, each with an explicit result:
- `unit_day`
- `resolution`
- `session_boundary`
- `overlap`
- an undefined pre-2020 activation or expiration (the unit fails and bar days wait as `awaiting definitions`)
- a history want with no definition want (refused at import)

The write is one `COPY` per unit inside the unit's ingest transaction, so a mid-unit failure leaves nothing partial.

### [NOTE] Completeness-surface scope is narrower than the architecture's principle, and the deferral is explained

The architecture principle says a tier ships with `status`, `coverage`, `health` and `accounting`. The slice ships `status` and `coverage` and defers `health`, `accounting` and `overview` to 305. It names the slice plan entry as the reason and is explicit that the bar section reads `load-complete only`. I'm not raising a finding because the deferral is documented and assigned to 305.

Load-complete here is the counts plus the UTC-day bound (D4), not the architecture's "first and last event time against the manifest". The manifest holds only the unit's UTC day and a record count, so this is a reasonable reading.

### [NOTE] The architecture still points at 302 D5 for the "no ticks wanted" marker, which this slice defers

The architecture says a bars-only product needs an explicit "no ticks wanted" marker "(302 D5)". The slice instead refuses history wants on `tier=None` entries and defers the marker to the `ohlcv-1s` slice. The deferral is consistent with the architecture's anticipated slices, since bars-only products don't exist until `ohlcv-1s`. The architecture's cross-reference is now stale and should be corrected there.

### [CONCERN] Interval-generic bar storage and projector are built for a slice that does not exist yet

This slice delivers only `ohlcv-1m`, but it builds parameterisation for the future `ohlcv-1s`:
- `BarStorage` objects and a `bar_constants.py` per-interval table, with a `BarProjector(storage)` class instantiated per interval.
- A ledger described as covering "every bar interval".
- A Value bullet, "A path for 1-second bars", that sells the abstraction.

Nothing in 302 uses a second interval. The `ohlcv-1s` slice is also gated on an unresolved purpose and an unbuilt "no ticks wanted" marker. The 1s table's chunk sizing is described as different, so a generic interface fixed now may not fit it.

What to cut or defer:
- Drop the `BarStorage` abstraction and the "one class per interval" framing. Ship a bar projector and storage constants for `ohlcv-1m` only.
- Let the `ohlcv-1s` slice extract the shared parts when a second interval exists.
- Keep `BAR_1M` as its own family (D1), since that rationale is real. Keep the separate constants module only if it is needed to stay under the file-size limit.
- Remove the 1-second path from the Value section.

The same trimming test applies to the `--json` `families` list and the `FAMILY_LEDGER` generality. Both are justified only if 303's family is genuinely next.

### [NOTE] The derived bar fixture is a computed stand-in, flagged as not purchased

The architecture says "never invented fixtures". The DBN v1 and v3 samples are real. The 2024-12-03 bar day is computed from a held `tbbo` day. It is marked **Not purchased**, follows 225's precedent for derived `trades` fixtures, and makes no parity claim. First-real-file risk is assigned to 304 in the Risk Assessment. This is acceptable.

### [NOTE] Parity measurement and manifest-scale timing are deferred to named owners

- **Parity:** tick-derived against provider bars goes to 306. It needs provider bars over a window where ticks are held, which first exists after 304.
- **Manifest-scale timing:** goes to 305, which matches the architecture's "one measurement precedes the history acquisition slice".
- **NFRs:** the architecture states no latency or throughput target for this path. The slice's only performance statement is that status adds no per-bar query, with the one bounded raw scan in `coverage`. That is enough.

### Run Digest

- Response length: 5968 chars
- Response is newline-free: no
- Tool calls made: 2
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 3
- Tokens — prompt / cached / cache write / completion / reasoning: 101659 / 11162 / 90493 / 4845 / not computed
- Duration: 40.3 s
- Cost: $0.41 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
