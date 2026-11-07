---
docType: review
layer: project
reviewType: slice
slice: first-real-purchase
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/304-slice.first-real-purchase.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261107
dateUpdated: 20261107
reviewedSha: ce5c32f5c1e89fac97f76dfa05abdc6afdccc714
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 113993
cachedTokens: 11162
cacheWriteTokens: 102827
completionTokens: 5470
durationSeconds: 49.9
costUsd: 0.467905
costBasis: provider
runId: run-20261107-p4-20aa594d
squadronVersion: 0.24.23
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Purchase scope and sizing match the architecture's purchase-test entry"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md#Technical Decisions"
  - id: F002
    severity: pass
    category: architecture-alignment
    summary: "Invoice-check deviation is explicit and recorded in the architecture"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md#D5 — Reconciliation: estimate, manifest, Databento's charge"
  - id: F003
    severity: concern
    category: scope-definition
    summary: "Success criteria 3 and 5 contradict the design's own expected findings"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md:292-294"
  - id: F004
    severity: concern
    category: proportionality
    summary: "The throwaway-database rehearsal duplicates checks that already exist"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md:249-257"
  - id: F005
    severity: concern
    category: proportionality
    summary: "The ceiling script is more machinery than a one-time two-key edit needs"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md:195-208"
  - id: F006
    severity: note
    category: boundaries
    summary: "Reconciliation reads production from the dev checkout"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md:127"
  - id: F007
    severity: note
    category: sizing
    summary: "The unit-count and request-count estimates are consistent"
    location: "project-documents/user/slices/304-slice.first-real-purchase.md:131"
---

# Review: slice — slice 304

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Purchase scope and sizing match the architecture's purchase-test entry

D1 buys `statistics` and `definition` for ES and GC over the full range, plus ES `ohlcv-1m` from 2017. The architecture's "Anticipated Slices" entry for the purchase test says the same. The prices ($0.158, $2.574, $0.452, $1.010) match the architecture's cost table. The $25 cap is restated with a specific per-pass ceiling of 25.00 and a 30-day ceiling of 35.00. The plan-inclusion marker is correctly treated as unset with no plan configured, so the guard counts the full list price. Dependencies point the right way: 300–303 are consumed, and 305, 306 and 307 are provided to.

### [PASS] Invoice-check deviation is explicit and recorded in the architecture

The architecture's Envisioned State says "the invoice agreed with the manifest". The slice compares the estimate with Databento's per-job charge only. It says why (the invoice is issued after month end) and defers the comparison to issue #69, which blocks 307. The architecture's slice entry already records this deferral. The slice also states that per-job charge and `actual_cost_usd` come from the same provider channel. That keeps the reduced claim honest.

### [CONCERN] Success criteria 3 and 5 contradict the design's own expected findings

Criterion 5 requires "ledger equal to raw in every session". D6 says some units are expected to fail and stay `retry_exhausted` or `awaiting definitions`. Examples are the `stat_reference` failure for ESZ9 on a Saturday and the `session_boundary` failure for 2018-08-06. A unit that fails ingest has no ledger rows, so the criterion cannot hold for those sessions. The walkthrough (line 347) and cutover step 4 (line 182) already accept exit 3 with failed units as a valid outcome. Criterion 3 ("every request delivered") also sits awkwardly with the "throttling or refusal" case, where a request can end `RETRY_EXHAUSTED`. As written, the slice can meet its design expectations and still fail its own acceptance test.
- Restate criterion 5: for every session whose units ingested, ledger equals raw. Every other session appears in the evidence note, classified by D6.
- Align criterion 3 the same way: delivered, or a named D6 failure.

### [CONCERN] The throwaway-database rehearsal duplicates checks that already exist

D8 builds a throwaway primary database and tick database, copies production's manifest, availability and definition rows read-only into them, and then runs `--estimate-only` twice. Its two assertions are already covered:
- The planned figure is at most $25 is already known from the free `get_cost` measurements in the Overview ($24.23). Cutover step 3 also stops before buying if the figure exceeds the per-pass ceiling, and the PM gate (D4) is set at 25.00.
- The base universe plans nothing is a property of the existing pass, and a unit test on the universe constants plus the "second `mt-run tick` plans nothing" criterion cover it.

The throwaway-database mechanics are the heaviest item in a slice rated 2/5. Cut D8 to a unit test over the planner using the existing fixtures, or keep only the "only the new wants, no `tbbo`" assertion. Keeping D1's fallback (start bars at 2018) as a pre-cutover edit is enough.

### [CONCERN] The ceiling script is more machinery than a one-time two-key edit needs

The architecture requires only that the PM sets the ceilings. The slice adds `deploy/set-tick-spend-ceilings.sh` with the following:
- input validation;
- a mode and owner re-assertion;
- a dated log file;
- test hooks for target, owner, log directory and privileged prefix;
- shellcheck;
- a pytest with four cases.

This is for one run that changes two values, and the slice itself says 307 may reuse the command. Cut it to a minimal check-then-act script: validate, replace the two keys, log old and new values. Defer the hook surface and the extra test cases until 307 needs a second use. If the full script stays, the slice should say what in this slice's evidence it protects.

### [NOTE] Reconciliation reads production from the dev checkout

The read-only session and free-provider-method constraints are stated and tested, and `--cap-usd` is never read from the dev `.env`. This is acceptable, but it is the one place the slice has a dev-checkout credential reach production state, against 234's "dev checkout stops being the production path". The docstring note for 307's reuse is the right mitigation. 307 should not extend the script to anything that writes.

### [NOTE] The unit-count and request-count estimates are consistent

The slice expects about 900 requests (4 × 197 months + 118 months) and about 19,000 units. This matches the session counts and the architecture's row counts (statistics 6.0 M + 13.4 M). The slice also lists failure handling for wait budget, unit time limit, throttling and death-after-submit, so the new I/O path is covered. It states no new throughput NFR, and the architecture has none for this path beyond spend.

### Run Digest

- Response length: 6080 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 113993 / 11162 / 102827 / 5470 / not computed
- Duration: 49.9 s
- Cost: $0.47 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7

## Resolution

- [pass] Purchase scope and sizing: no change needed.
- [pass] Invoice-check deviation: no change needed.
- [concern] Success criteria 3 and 5: changed. Criterion 3 (now 2) accepts a request delivered or ending with a named failure, and every unit ingested or failed with a reason D6 classifies. Criterion 5 (now 4) requires ledger equal to raw only in sessions whose units ingested, and puts every other session in the evidence note, classified by D6.
- [concern] Throwaway-database rehearsal: removed D8 and its walkthrough step. The one assertion worth keeping, "only D1's wants, no `tbbo`", is now a stop condition on the cutover's free estimate (D3 step 3). The fallback to 2018 bars triggers when that estimate exceeds $25 (the cutover stops before buying, and a fix release follows). The migration plan cites the universe unit test, that estimate check, and the "second run plans nothing" criterion.
- [concern] Ceiling script: cut to the minimum. It validates, replaces the two keys in place (so the mode and owner are kept), and prints old and new values. It has one test hook (target path) and one pytest. The re-asserted mode, the dated log file, the other hooks and the extra test cases are removed, not deferred, so no issue was filed.
- [note] Reconciliation reads production from the dev checkout: no change. The design already makes the session read-only and calls only free provider methods, and the script's docstring tells 307 never to make it write.
- [note] Unit and request counts: no change needed.
