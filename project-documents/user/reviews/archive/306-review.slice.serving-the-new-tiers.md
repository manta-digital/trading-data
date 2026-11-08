---
docType: review
layer: project
reviewType: slice
slice: serving-the-new-tiers
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/306-slice.serving-the-new-tiers.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261108
dateUpdated: 20261108
reviewedSha: f9ed0cdd086bc309990de63eafce4675bf8c54f5
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 7
effort: high
turns: 8
promptTokens: 513694
cachedTokens: 405489
cacheWriteTokens: 108193
completionTokens: 8735
durationSeconds: 79.0
costUsd: 0.561466
costBasis: provider
runId: run-20261108-slices-plan-d03f6d49
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: pass
    category: architectural-alignment
    summary: "Bar-source rule matches the architecture"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#Overview"
  - id: F002
    severity: concern
    category: integration
    summary: "Roll basis for bars versus `/schedule` is left ambiguous, and D6 depends on it"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#D3 and #D6"
  - id: F003
    severity: concern
    category: interface-specification
    summary: "Parity needs two families' verdicts, but D3 takes one family"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#D7 and Data Flow (Parity)"
  - id: F004
    severity: concern
    category: performance-nfr
    summary: "Product statistics read volume is not bounded or measured"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#D6 (Bound, Read) and #D10"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Parity command and statistics route carry more mechanism than the slice needs"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#D7 and #D6"
  - id: F006
    severity: note
    category: data-semantics
    summary: "Close-stamped one-minute stub buckets ship as a documented wart"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#Special Considerations"
  - id: F007
    severity: note
    category: dependencies
    summary: "First parity measurement depends on 304's production ingest"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#D9"
  - id: F008
    severity: pass
    category: error-handling
    summary: "Failure modes and admission bounds are explicit for the new I/O paths"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#D7 and #D10"
---

# Review: slice — slice 306

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Bar-source rule matches the architecture

The design applies the "Which source serves bars" rule and the "never splice tiers silently" principle as written. These are the points that match:
- Provider `ohlcv-1m` is the only source of served time bars, and 5m through 1d are rolled up on the CME session.
- Ticks never produce served bars, including where no provider bars are held (D2 rejects the fallback).
- Read-time tick bars survive only as `mt data tick parity`.
- Every response names its source (`BAR_SOURCE`).
- Provider `ohlcv-1h` and `1d` are neither archived nor served, and their bucketing is checked and recorded in D1. That is the check the slice plan assigns to this slice's design.

All of the 306 plan entry is covered: bars, source naming, statistics routes (a decision to serve), docs, and the D1 check. Dependency directions are correct, and 308 gets a bars path that does not read `tick_trade`.

### [CONCERN] Roll basis for bars versus `/schedule` is left ambiguous, and D6 depends on it

D3 gives the plans a `family` parameter and makes `held_session_bounds` take it. The schedule route and every existing caller keep passing `TICK_TIER`. Two integration questions follow, and the design answers neither.
- **Deep history:** the architecture's main goal is history back to 2010, which exists as bar-only ranges. D6 says a continuous open-interest series is "the consumer's join with `/schedule`'s segments". The design never says whether `/schedule` resolves `v.0` and `n.0` over a range with no ticks. If its default range and held bounds come from the tick ledger, the join D6 prescribes fails exactly where the new tiers matter.
- **Same rule, different basis:** for a session where both tiers are held, `/bars` (family `BAR_1M`) decides the volume rule on bar volume, and `/schedule` (`TICK_TIER`) on tick volume. The same `rule=v.0` can then give a different contract in the two responses for near-tied sessions. The design says only that the "schedule is unchanged" and that `segments[].basis` reports the basis.

Fix: state which basis `/schedule` uses over bar-only and mixed ranges, and state whether bars and schedule agree on a mixed range. Add a test that joins statistics with schedule segments over a bar-only window.

### [CONCERN] Parity needs two families' verdicts, but D3 takes one family

The parity data flow plans with `family=TICK_TIER` and then restricts to sessions "complete in TICK_TIER and in BAR_1M". D3 says the verdict reader is derived from the single family passed to the plan, "so a family and a reader can never be passed in a mismatched pair". So the `BAR_1M` verdicts are not available from that plan, and the design does not say where they come from. If parity makes a second plan call, or calls `session_verdicts` directly, that sidesteps the D3 guarantee. Say how parity gets both verdicts, for example a second `session_verdicts(BAR_1M)` call joined by session inside the same snapshot.

### [CONCERN] Product statistics read volume is not bounded or measured

The pre-read bound in D6 caps output rows (sessions × listed outrights). The read itself is one `resolved` call over `[first, last]` with all three stat types, and "filter (… outrights listed in the session)" happens afterwards. So what the database reads is not what is bounded. A 12-year GC window (the stated admitted maximum) resolves every instrument's stats, including spreads and other non-outright instruments, over about 3,000 sessions, and then discards most of it. D10 measures only the 1d bars worst case, and the statistics path has no latency target against the 20 s statement timeout. The failure mode is a 504, but this slice would admit requests it knows can hit it, and nothing measures how likely that is.
Two options:
- Push the instrument filter into the `resolved` query.
- Add the worst admitted statistics read to D10 with the same under-10 s bound, and tighten the admitted range if it fails.

### [CONCERN] Parity command and statistics route carry more mechanism than the slice needs

Effort is 3/5, yet the slice adds a plan-family refactor, a new reader with rollup, a statistics plan module, two routes with models, a pre-read bound computed from `outrights`, and a new CLI command with a five-way classification and two output forms. Candidate cuts:
- **Parity text and JSON forms:** the text form lists "the first 20 non-agreeing minutes per session" while `--json` lists all. Keep per-session counts and close-minute lines in text, and put the full list only in `--json`. That removes the truncation rule and its tests.
- **D10 batching contingency:** this is prepared scope for a measurement that has not failed. Keep it as a one-line fallback, not as designed scope with acceptance criteria. Criterion 11 ("or the batched statement is in place") makes it a deliverable.
- **Product statistics route:** the plan entry requires "routes, or a recorded decision not to serve". The contract route alone satisfies it. The product route needs the most machinery (the `outrights` pre-read, the bound, and required-range handling). Keep it only if trading-engine's roll work needs the curve in this slice. Otherwise defer it to 307's follow-up.

### [NOTE] Close-stamped one-minute stub buckets ship as a documented wart

A provider bar stamped exactly at `close_ns` becomes a one-minute bucket at 5m, 15m and 1h on regular sessions. This sits awkwardly with the stated value that "a bar means the same thing in every era". The design discloses it in the CHANGELOG and `reference.md` §6.1, and ties the rule to issue #70, so it is acceptable here. The design also says any rule change belongs to `locate_ns`, which moves ingest and serving together, so no rework is hidden in this slice.

### [NOTE] First parity measurement depends on 304's production ingest

Criterion 8, the walkthrough and the closing of issue #70 all need 304's ES 2024 bars ingested in production. D9 correctly keeps this out of Phase 6, so there is no build-time dependency. Slice frontmatter lists only 302 and 303, so the Phase 7 dependency on 304's data is carried only in prose. It should be stated as a precondition of the walkthrough, which D9 and the Integration Points section already do.

### [PASS] Failure modes and admission bounds are explicit for the new I/O paths

Failure handling is concrete in these places:
- **Parity:** a single snapshot, the report printed only after the last comparison, and an exit-code mapping (1 for preflight, 4 for storage, 3 only for a completed comparison with findings).
- **Statistics:** errors map through `tick_errors`, giving 503 and 504 as on the other futures routes, and the row-ceiling refusal is a 422 before the read.
- **Bars with no provider rows:** listed in `incomplete`, never filled from ticks.
- **Bars worst case:** the 1d, 1,300-session read has a stated 10 s bound against the 20 s statement timeout.

The architecture states no numeric serving NFR, so there is no restatement gap.

### Run Digest

- Response length: 8057 chars
- Response is newline-free: no
- Tool calls made: 7
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: high
- Turns: 8
- Tokens — prompt / cached / cache write / completion / reasoning: 513694 / 405489 / 108193 / 8735 / not computed
- Duration: 79.0 s
- Cost: $0.56 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
