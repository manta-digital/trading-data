---
docType: review
layer: project
reviewType: slice
slice: cme-calendar-history
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/301-slice.cme-calendar-history.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261101
dateUpdated: 20261101
reviewedSha: 74bff228fa23ad1199adec4bc0b273bfaa6e2ab2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 98743
cachedTokens: 11162
cacheWriteTokens: 87577
completionTokens: 4371
durationSeconds: 39.1
costUsd: 0.395891
costBasis: provider
runId: run-20261101-p4-56a09d5c
squadronVersion: 0.24.11
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Alignment with the architecture's session-day and dataset-start commitments"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md#Technical Decisions"
  - id: F002
    severity: concern
    category: scope-proportionality
    summary: "`week_open` is built on an unconfirmed forum lead, which the slice rejects elsewhere"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md:146-163"
  - id: F003
    severity: concern
    category: error-handling
    summary: "The count-check script has no failure-mode handling beyond \"exit 2\""
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md:221-231"
  - id: F004
    severity: concern
    category: scope-proportionality
    summary: "The count check is retained as a permanent script that overlaps later completeness work"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md:221-231"
  - id: F005
    severity: concern
    category: hidden-dependency
    summary: "Migration 301 and migrations 058/059 depend on mutable application code"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md:203-219"
  - id: F006
    severity: concern
    category: integration-points
    summary: "The frontmatter `interfaces` list omits 308, which the slice says it provides to"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md:7-13"
  - id: F007
    severity: note
    category: error-handling
    summary: "The cutover does not state timer behaviour on a mid-run failure"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md:236-239"
  - id: F008
    severity: note
    category: architecture-alignment
    summary: "The architecture-level framing matches the slice's constraints"
    location: "project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md"
---

# Review: slice — slice 301

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Alignment with the architecture's session-day and dataset-start commitments

The architecture says a trading day is the CME session and that "slice 301 dates regular hours". D1–D3 do this with dated eras and leave `trading_sessions` as the single read surface. The goal of bars from 2010-06-06 is met by `CME_DATASET_START`, defined once (D5). Principles are respected: no guessed rows (D2, D5, D8 rejections), no fallback to `trading_calendars` hours for dated calendars (D3), and a 2020-onward before-and-after proof inside the migration (D6). Excluding NYSE and NASDAQ, intra-session halts, and the ETH/ALL paths keeps the slice from creeping into other scope.

### [CONCERN] `week_open` is built on an unconfirmed forum lead, which the slice rejects elsewhere

D1 adds a NOT NULL `week_open` column and a three-way open-time precedence. Its stated justification is a forum lead that the Sunday open differs from the daily reopen. D1 rejects a distinct Friday close because "no document read so far shows one". D2 also says a forum lead is "not a source". The same standard should apply to `week_open`. If no dated document shows a divergent Sunday open, the column, `HoursEra.week_open`, the precedence rule, and the Monday-open test are unused abstraction. If sourcing does show one, add the column then, in the same migration, as D1 already proposes for the Friday case. Fix: make the sourcing of Sunday-versus-daily-open an explicit first step, and have the slice carry `week_open` only if a document confirms it. Otherwise cut it.

### [CONCERN] The count-check script has no failure-mode handling beyond "exit 2"

The check makes two Databento metadata calls per session over about 2,500 sessions per calendar, which is roughly 10,000 calls in total. This is a new I/O path against a remote provider. The only failure handling is "exit 2 on an input or provider error". The doc does not say what happens on a mid-run timeout, an HTTP 429 or rate limit, a transient 5xx, or a partial run. It also doesn't say whether a run resumes or restarts. Without a timeout, retry and backoff policy, and resume behaviour, one transient error forces a full rerun of a 10,000-call job, or the script hangs. Success criterion 8 depends on a complete run. Fix: add a per-call timeout and a bounded retry for rate-limit and transient errors. State whether `--start` and `--end` are the resume mechanism. State that a call that still fails after retries is reported per session and is never counted as a zero.

### [CONCERN] The count check is retained as a permanent script that overlaps later completeness work

The architecture puts per-session completeness verdicts in later slices. 305 and the "Futures completeness surfaces" slice own them, and `verify_cme_sessions.py` already exists for ingest placement. D8 adds a permanent script with its own exit-code contract and window semantics. Its job is a one-time gate before the 304 and 307 purchases. That is valid evidence-gathering, but a lighter option meets the need. The same logic could be a one-off run whose findings go into the implementation notes. A retained script would be justified only if 305 reuses it, and the design does not say so. Fix: either state that 305 consumes the script, or reduce it to a minimal tool without the summary and exit-code surface. The stub-adapter unit tests can stay either way.

### [CONCERN] Migration 301 and migrations 058/059 depend on mutable application code

Step 5 of the migration calls `extend_calendar_sessions`, which reads `trading_calendar_hours`. D7 has 058 and 059 reuse the shared population function and the upsert helper. Migration output therefore depends on code that later slices may change. D7 pins hours, but not the population logic. The 058/059 equality is proven by tests today and holds only while those tests pass. This is the existing pattern in 058, so it is not a blocker. The design should name the regression test that protects 058/059 output on a fresh database. It should also state that later changes to `populate_trading_sessions` must keep it passing, so the dependency is explicit and not hidden.

### [CONCERN] The frontmatter `interfaces` list omits 308, which the slice says it provides to

The frontmatter lists interfaces 302 to 307. "Provides to Other Slices" says that "306 and 308" read session open and close from `trading_sessions`. The architecture's tick-bar count restarts at each session open (308). Either add 308 to `interfaces`, or state that 308 is covered through 306's read path. As written, the dependency graph and the integration section disagree.

### [NOTE] The cutover does not state timer behaviour on a mid-run failure

D9 holds the timers across install and migrate. It does not say whether the timers are released if the migrate or verify step fails. D7 relies on the timers being held for safety, because extending a CME calendar fails between install and migrate. If `cutover_common` already handles release or hold on failure, a one-line reference to that behaviour is enough. If it doesn't, the doc should say that timers stay held on failure and who releases them.

### [NOTE] The architecture-level framing matches the slice's constraints

The architecture lists no calendar slice among its "Anticipated Slices", but its "What a day is" section defers era dating to 301. The slice's provides-to list (302, 303, 304, 307) matches the architecture's need for sessions that place pre-2020 bars and statistics. No NFR with a numeric target in the architecture touches this path, so none needs restating.

### Run Digest

- Response length: 6958 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 98743 / 11162 / 87577 / 4371 / not computed
- Duration: 39.1 s
- Cost: $0.40 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
