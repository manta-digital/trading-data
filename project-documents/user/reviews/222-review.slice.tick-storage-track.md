---
docType: review
layer: project
reviewType: slice
slice: tick-storage-track
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/222-slice.tick-storage-track.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260928
dateUpdated: 20260928
reviewedSha: 40734c1c1c716af41800f59407288cdaec47a831
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 18
squadronVersion: 0.15.0
findings:
  - id: F001
    severity: pass
    category: architectural-alignment
    summary: "Natural-key supersession is measured, disclosed, and process-compliant"
    location: "project-documents/user/slices/222-slice.tick-storage-track.md:210-249"
  - id: F002
    severity: pass
    category: architectural-alignment
    summary: "Realtime-paths check present as the architecture mandates"
    location: "project-documents/user/slices/222-slice.tick-storage-track.md:781-791"
  - id: F003
    severity: pass
    category: dependency-direction
    summary: "Dependency direction and isolation honored"
    location: "project-documents/user/slices/222-slice.tick-storage-track.md:128-137"
  - id: F004
    severity: concern
    category: architectural-alignment
    summary: "Removing tier from the ledger reinterprets an explicit architecture statement without a read-path cost analysis"
    location: "project-documents/user/slices/222-slice.tick-storage-track.md:403-416"
  - id: F005
    severity: note
    category: error-handling
    summary: "No new I/O paths in this slice — failure-mode enumeration criterion is not applicable"
    location: "project-documents/user/slices/222-slice.tick-storage-track.md#Technical-Scope"
  - id: F006
    severity: note
    category: nfr-restatement
    summary: "Chunk-interval NFR is correctly restated with a concrete target"
    location: "project-documents/user/slices/222-slice.tick-storage-track.md:417-437"
---

# Review: slice — slice 222

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [PASS] Natural-key supersession is measured, disclosed, and process-compliant

The architecture's Architectural Principles fix the natural key as the provider's `(instrument, event time, sequence)` with "the project adds nothing to it." The slice measures real adopted-file collisions (196,202 of 10,049,172 trades, 1.95%) and shows conflict-ignore on that key would silently drop real fills — exactly the "never use silent fallback values" failure this project's conventions forbid. The fix (`sequence_ordinal`) is deterministic, preserves the architecture's unit-idempotence claim, and is explicitly listed under "Architecture statements this design supersedes" with a commitment to record it in the slice plan's Notes. This is the correct way to handle an architecture/data conflict: evidence first, explicit disclosure, minimal-footprint fix.

### [PASS] Realtime-paths check present as the architecture mandates

The architecture requires "every 220 slice design carries a short 'realtime paths' check naming any choice that forecloses either path." The slice includes this section and correctly identifies that `sequence_ordinal` (delivery-order based) and delete-by-unit supersession remain compatible with a future live-segment unit.

### [PASS] Dependency direction and isolation honored

The architecture's isolation principle forbids source→source imports ("never imports another source"). The slice explicitly re-derives the list-of-dicts/`_in_list` pattern into its own tick copy "because the architecture forbids importing from `data/kalshi`," and confirms via 923 that grants, credentials, and the migration track are fully separate from the primary database. Verified against 923's slice doc (roles, artifact, track routing) — no hallucinated integration points.

### [CONCERN] Removing tier from the ledger reinterprets an explicit architecture statement without a read-path cost analysis

The architecture (Envisioned State, "Storage") states plainly: the tier "is recorded on each archive unit **and ledger row**." Technical Decision 7 removes it from the ledger, reasoning only that a duplicate value "could disagree with it," and asserts the architecture's requirement "is satisfied by reaching it through the unit" — which is actually two joins away (`tick_ingest_ledger.unit_id → tick_archive_unit.request_id → tick_request.schema`). This is a reasonable normalization call, and it is disclosed under "Architecture statements this design supersedes," but the slice gives no analysis of what this costs downstream — 228/229 status and coverage surfaces, and the project already has NFR-driven precedent (167) for keeping status/coverage reads fast. If a tier-aware session listing is ever wanted from the ledger alone, this decision forces a join across two tables for something the architecture apparently wanted denormalized on purpose. Recommend the slice note explicitly (or 227-229's design confirm) that the extra join has no measurable cost on the surfaces that will use it, rather than asserting equivalence by fiat.

### [NOTE] No new I/O paths in this slice — failure-mode enumeration criterion is not applicable

The slice is schema/DDL-only: Excluded explicitly rules out "writing any row outside tests" (223/224 own writes) and no network, socket, or message-passing surface is introduced. Migration application itself reuses 923's existing framework (misroute guard, ledger-based apply), not a new I/O path. The reviewer criterion requiring enumerated hang/timeout/disconnect handling for new I/O paths therefore does not apply to this document; I confirmed this is a deliberate scope boundary rather than an omission.

### [NOTE] Chunk-interval NFR is correctly restated with a concrete target

The architecture's "chunk geometry is derived from wall-clock span, never from volume" principle is restated with the specific rule (≈20-year span ÷ 1,000–2,000 target chunks), the resulting value (7 days / 604,800,000,000,000 ns), and volume sanity-checked against measured ES daily counts. The coincidental match to `MINUTE_OHLCV_CHUNK_INTERVAL` is noted as cross-validation, not as literal sharing, so it does not conflict with the architecture's "never shares... chunk geometry" isolation constraint (the tick and minute hypertables remain fully separate, on separate databases).

### Run Digest

- Response length: 5199 chars
- Response is newline-free: no
- Tool calls made: 18
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
