---
docType: review
layer: project
reviewType: slice
slice: cme-session-model-and-data-correctness-amendment
targetKind: slice
rulesSource: project
project: trading-data
verdict: PASS
verdictSource: stated
sourceDocument: project-documents/user/slices/221-slice.cme-session-model-and-data-correctness-amendment.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260928
dateUpdated: 20260928
reviewedSha: 982b9ab02f8afe0faf9b0ec1725f277e4f19ffcf
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 12
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Scope stays within the architecture's stated carve-out"
    location: "project-documents/user/slices/221-slice.cme-session-model-and-data-correctness-amendment.md#Overview"
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Architecture-mandated verification item resolved and fed back correctly"
    location: "project-documents/user/slices/221-slice.cme-session-model-and-data-correctness-amendment.md:139-145"
  - id: F003
    severity: pass
    category: uncategorized
    summary: "I4 boundary respected — one function, calendar as data"
    location: "project-documents/user/slices/221-slice.cme-session-model-and-data-correctness-amendment.md:96"
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Regression protection matches architecture's explicit requirement"
    location: "project-documents/user/slices/221-slice.cme-session-model-and-data-correctness-amendment.md#Success-Criteria"
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Contract amendment content matches the architecture's own text"
    location: "project-documents/user/slices/221-slice.cme-session-model-and-data-correctness-amendment.md:224-246"
  - id: F006
    severity: note
    category: error-handling
    summary: "Verification-script failure handling is terse but the I/O surface is low-risk"
    location: "project-documents/user/slices/221-slice.cme-session-model-and-data-correctness-amendment.md:323"
  - id: F007
    severity: pass
    category: uncategorized
    summary: "No NFR omission — throughput/latency targets correctly deferred to consuming slices"
    location: "project-documents/user/slices/221-slice.cme-session-model-and-data-correctness-amendment.md"
---

# Review: slice — slice 221

**Verdict:** PASS
**Model:** claude-sonnet-5

## Findings

### [PASS] Scope stays within the architecture's stated carve-out

The slice touches only the production database's minute migration track and the calendar tables I4 already governs, exactly the "Session model" carve-out the architecture names (220-arch, Architectural Principles, "Operational state has one home"). No tick database, purchase, ingest code, or ledger is touched — matching the architecture's Anticipated Slices note that "the first slice needs no tick-database table."

### [PASS] Architecture-mandated verification item resolved and fed back correctly

The architecture explicitly flagged "ES and GC both trade Sunday–Friday 17:00–16:00 Chicago time, so one row serves both — verify per product at slice design" (220-arch, "Session model"). D1 correctly performs that verification, finds the assumption false (holiday halts differ), and the slice records the correction both in its own Integration Points/Follow-ups section and as a required edit to the slice-plan's "Architecture statements superseded" list — the proper channel for revising an architecture assumption discovered at design time, not a silent deviation.

### [PASS] I4 boundary respected — one function, calendar as data

D3/D5 explicitly reject adding a second session-calculation implementation (`pandas_market_calendars` stays a scratchpad cross-check, "never a project dependency, because a second session implementation in the tree would itself breach I4"), and all new lookup methods land on the existing `TradingCalendar` class. This matches I4's text in the data-correctness contract ("There is exactly one such function in the codebase... handled by data in the calendar table, not by special-case code paths").

### [PASS] Regression protection matches architecture's explicit requirement

The architecture states "[t]he open-after-close change to `populate_trading_sessions` is guarded by a regression test that `NYSE` rows are unchanged (slice 221)" (220-arch, "Session model"). The slice delivers exactly this (Success Criteria #4, integration test `nyse_sessions_unchanged`), and additionally freezes a pre-change fixture before touching code (Implementation Notes step 1) — a stronger guarantee than the architecture required.

### [PASS] Contract amendment content matches the architecture's own text

D10's I7 exception wording, the sequence-gap-detection move, and the manifest-based tick-completeness vocabulary rule are close paraphrases of 220-arch's "Data-correctness contract amendment" section and Architectural Principles ("Provider records are preserved"). New invariants I11–I14 map cleanly onto architecture principles already stated (manifest-based completeness, per-row unit provenance/supersession, session-assigned ticks, explicit futures identity) rather than inventing new guarantees.

### [NOTE] Verification-script failure handling is terse but the I/O surface is low-risk

"If a file fails to decode, the script fails loudly" is the only failure-mode statement for `scripts/verify_cme_sessions.py`'s file reads. This slice introduces no network I/O, no new message types, and no peer-connection paths (those belong to 223/224); the script is a one-off local-file developer tool, not a production I/O path, so the abbreviated treatment is proportionate. Worth one added sentence at design or task-breakdown time (e.g., missing file vs. corrupt zip vs. decode error) since Success Criterion 8 relies on this script's exit code as an acceptance gate, but this does not block the slice.

### [PASS] No NFR omission — throughput/latency targets correctly deferred to consuming slices

The architecture's only latency/throughput NFRs on this data path ("Ingest throughput and the write path") are scoped to the ingest pass (224), which this slice explicitly excludes ("Any tick table, ingest code, or ledger... 224 uses it"). 221 provides a vectorized lookup (`SessionIndex.locate_ns`) as an interface but makes no throughput claim of its own, and the architecture does not impose one on the session-lookup surface itself, so there is no unstated NFR to restate here.

### Run Digest

- Response length: 5105 chars
- Response is newline-free: no
- Tool calls made: 12
- Tool calls failed: 0
- Stop reason: end_turn
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
