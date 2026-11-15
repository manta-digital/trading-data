---
docType: review
layer: project
reviewType: slice
slice: time-bars-from-held-bars-and-ticks
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md
aiModel: claude-opus-5-5
aiProfile: sdk
status: complete
dateCreated: 20261115
dateUpdated: 20261115
reviewedSha: e6c89dbed5c0232e0e2c75846b4ee460b21ae8d4
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 5
turns: 6
promptTokens: 301916
cachedTokens: 206867
cacheWriteTokens: 95041
completionTokens: 5729
durationSeconds: 68.1
costUsd: 0.917085
costBasis: provider
squadronVersion: 0.24.28
findings:
  - id: F001
    severity: concern
    category: scope
    summary: "The automatic minute comparison is deferred, but the slice plan assigns it to this slice"
    location: "project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md:98-102"
  - id: F002
    severity: concern
    category: under-specification
    summary: "\"Held\" is undefined for both provider bars and ticks"
    location: "project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md:64-68"
  - id: F003
    severity: concern
    category: error-handling
    summary: "No failure handling for the tick-cut read"
    location: "project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md:74-78"
  - id: F004
    severity: note
    category: scope
    summary: "Doc and issue housekeeping goes beyond the slice plan entry"
    location: "project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md#documents-and-issues-cleaned-up-in-this-slice"
  - id: F005
    severity: note
    category: integration
    summary: "`source` → `sources` is a breaking change under `/api/v1`"
    location: "project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md:51-54"
  - id: F006
    severity: pass
    category: alignment
    summary: "Source order, size grammar and bucketing match the architecture"
    location: "project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md:38-70"
  - id: F007
    severity: pass
    category: design
    summary: "One implementation of the tick-cut statement and cost admission from the ledger"
    location: "project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md:72-80"
  - id: F008
    severity: pass
    category: dependencies
    summary: "Dependencies and removals point the right way"
    location: "project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md:5-8"
---

# Review: slice — slice 309

**Verdict:** CONCERNS
**Model:** claude-opus-5-5

## Findings

### [CONCERN] The automatic minute comparison is deferred, but the slice plan assigns it to this slice

The slice plan entry for 309 says: "The comparison of tick-built minutes with provider minutes becomes an automatic background check where both are held, and is no longer a command." Architecture principle "Never splice sources silently" and "Parity between tiers" both say agreement is measured "by an automatic background check and not by a command an operator types." The slice removes the command and builds no check. It defers the check "to when provider bars are first held, as part of that work", and no slice in the plan owns that work. After this slice, the comparison cannot run at all, by hand or automatically.

The reason given ("nothing to prove it works on") does not hold. Success criterion 3 already needs a test database that holds provider one-minute bars and ticks for the same session. Slice 302 also built a real-shape bar day from a held `tbbo` day. Both are enough to build and test the pass-time comparison: compare once per session that is complete in both, store the counts, report them in `mt data health`.

Fix: build the check here and test it on the criterion-3 fixture. Otherwise, amend the slice plan so a named slice or a Future Work entry owns it, and say in this doc that the architecture principle is unmet until then.

### [CONCERN] "Held" is undefined for both provider bars and ticks

Source choice depends on "held complete in a provider bar size" and "the session's ticks are held". The architecture separates **load-complete** (ledger counts and first/last event time agree with the manifest) from **content-complete**. It also notes that a session spans two UTC-day units. The design does not say which verdict decides "held", or what happens when only one of a session's two units is ingested, for either tier.

The edges of the held ES and GC ranges are exactly that case. If the rule is too loose, partial bars are served under a `ticks` or `ohlcv-1m` label that suggests they are complete. If it is too strict, sessions that could be served are listed in `incomplete`.

Fix: state the rule. One option: provider bars are held for a session when every unit covering it is load-complete in `tick_bar_ledger`. Ticks are held when every covering unit is verified in the ingest ledger. A session partly held in one tier falls through to the next tier. Name the `incomplete` reason used when neither tier qualifies.

### [CONCERN] No failure handling for the tick-cut read

The tick-cut statement is a new heavy read path: about 393k trades per ES session, run once per session in a range. The design covers admission, meaning a refusal before any tick is read. It says nothing about what happens after admission:

- a statement that runs past a time budget (statement timeout or none, and the resulting status code);
- a client that disconnects mid-read (is the remaining per-session work cancelled, or does it keep running for every session in the range);
- a session whose ledger trade count is missing or disagrees with the rows, which matters because admission trusts the ledger.

Admission bounds the expected cost but not the actual cost. The limit is also set from one production measurement.

Fix: add a short failure-mode list for this path, with the handling for each case. That might be a statement timeout that returns a named error, cancellation of the remaining session reads on disconnect, and refusal or an `incomplete` entry when the ledger has no count for a session.

### [NOTE] Doc and issue housekeeping goes beyond the slice plan entry

The slice plan asks for the provider-only behaviour to be removed from "code, tests and API documents". This slice also rewrites the 302–308 slice and task files and closes #69 and #74, which are unrelated to time bars. This work is small and keeps the documents consistent with the ADR, so it does not need cutting. It could be a separate `docs:` commit so the slice diff stays focused.

### [NOTE] `source` → `sources` is a breaking change under `/api/v1`

Renaming a scalar response field to a list breaks any existing reader. The architecture records that no consumer calls the futures routes yet (Current State, "Consumers"), so this is acceptable. Success criterion 8 covers telling trading-ui and trading-engine about the change. Saying in the doc why no version bump is needed would head off the question.

### [PASS] Source order, size grammar and bucketing match the architecture

These parts match "Which source serves bars" and the ADR:

- Provider bars are used first where held. The read checks whether the held size divides the requested size, which matches "the same size, or a finer one added up".
- Seconds sizes correctly fall to ticks while only one-minute bars are held.
- `1d` is one bar per CME session, and provider `1h`/`1d` are not used.
- Buckets are anchored at the session open, and no bar crosses a session (#47).
- `sources` is reported per session run, which is a correct reading of "never splice silently" for requests that cross sessions.
- `applied` echoes defaults (#77).
- Bars of N trades and raw ticks are untouched.

### [PASS] One implementation of the tick-cut statement and cost admission from the ledger

Moving slice 306's minute statement into a parameterised module, and having the comparison call it, follows DRY. The "no SQL second implementation" rule covers the N-trade rollup, not time bars, so this does not conflict with it. Admission by trade count from the ingest ledger follows the slice-308 pattern and the principle that completeness comes from the manifest and ledger. The limit's default is set by measurement, not guessed. Stored aggregates are correctly left out (ADR decision 7).

### [PASS] Dependencies and removals point the right way

The slice depends on 302 (the bar table and ledger) and 308 (the data-kind plumbing kept from 306). Both are complete, and no completed slice is made to depend on 309. Removing the production history wants carries out ADR decision 6 and the slice plan's "The production configuration wants no history". The health, accounting and overview rows disappearing as a result is consistent, because those rows are printed per want.

### Run Digest

- Response length: 7816 chars
- Response is newline-free: no
- Tool calls made: 5
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 6
- Tokens — prompt / cached / cache write / completion / reasoning: 301916 / 206867 / 95041 / 5729 / not computed
- Duration: 68.1 s
- Cost: $0.92 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
