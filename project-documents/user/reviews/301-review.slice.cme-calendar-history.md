---
docType: review
layer: project
reviewType: slice
slice: cme-calendar-history
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/301-slice.cme-calendar-history.md
aiModel: xiaomi/mimo-v2.6-pro
aiProfile: openrouter
status: complete
dateCreated: 20261101
dateUpdated: 20261101
reviewedSha: f6c314ebecd0d654cc9756c35e4c6719f9ff7a69
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 8
turns: 6
promptTokens: 148295
cachedTokens: 94208
completionTokens: 31841
reasoningTokens: 29249
durationSeconds: 716.2
costUsd: 0.051606
costBasis: alias_pricing
squadronVersion: 0.24.10
findings:
  - id: F001
    severity: pass
    category: alignment
    summary: "Scope, dependencies and effort match the slice plan entry; interfaces match the session model"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Era-lookup miss case is unspecified in the shared session-population path"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md:103"
  - id: F003
    severity: concern
    category: consistency
    summary: "Unbounded era-entry rule conflicts with the \"2020 onward untouched\" guarantee"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md:107"
  - id: F004
    severity: note
    category: error-handling
    summary: "`ON CONFLICT DO NOTHING` keeps a differing pre-existing holiday row silently"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md:87"
  - id: F005
    severity: note
    category: integration
    summary: "Slice 305 is missing from the provided-interfaces list"
    location: "project-documents/user/slices/301-slice.cme-calendar-history.md:141"
  - id: F006
    severity: note
    category: consistency
    summary: "Parent architecture states the session shape as one fixed window"
    location: "project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md:74"
---

# Review: slice — slice 301

**Verdict:** CONCERNS
**Model:** xiaomi/mimo-v2.6-pro

## Findings

### [PASS] Scope, dependencies and effort match the slice plan entry; interfaces match the session model

The design delivers every element plan entry 301 assigns (`300-slices...md` item 2): dated regular hours in `trading_calendar_hours`, one sourced row per pre-2020 holiday/early close/unscheduled closure, nothing generated from a holiday rule, a before/after proof that 2020+ is untouched, queryable session open/close per calendar and date, no purchase, no tier change. Nothing assigned is dropped or deferred. The one item beyond the plan's wording — extending `scripts/verify_cme_sessions.py` to pre-2020 dates — serves success criterion 7 and the verification the plan expects at 304/307 ("every bought day goes through the same check"), so it is in scope, not creep. `dependencies: []` matches "Dependencies: none within this plan"; effort 3/5 matches. The named interfaces (`populate_trading_sessions`, `extend_calendar_sessions`, `SessionIndex`, `locate_sessions`, `session_boundary`, `verify_cme_sessions.py`) match the 221/232 session model as recorded in `221-slice.cme-session-model-and-data-correctness-amendment.md` and the data-correctness reference. D1's rejection of in-code hours (overwritten by `mt data extend` and repair rebuilds) is consistent with 220's rule that calendar facts live in the calendar tables, and keeping hours in data honours 220's "hard-coded CME hours are the defect". 302's stated expectations (`CME_DATASET_START` as the single dataset-start definition, pre-2020 sessions via `locate_sessions`, no calendar migration in 302) all match this slice's provides.

### [CONCERN] Era-lookup miss case is unspecified in the shared session-population path

D1 (line 103) and the data flow (line 90) say `populate_trading_sessions` "uses the era in force on each session's date" for calendars with eras, but never state what happens when **no** era is in force. This is reachable, not hypothetical: `extend_calendar_sessions` takes caller-supplied `start`/`end` (221: `mt data extend`, and the repair paths that rebuild from the tables), 221's clamp bounds only `end` via `holidays_seeded_through`, and this slice explicitly excludes "hours before 2010-06-06" (Technical Scope, Excluded) — so a date before the first era's `effective_from` lands on an undefined lookup. The same hole covers the documented risk outcome where sourcing fails and the fill "stops at that era and says so" (line 107; Risk Assessment): the unsourced range is left session-less, yet `CME_DATASET_START` — the constant 302's `HistoryWant` gates on (line 143) — still advertises 2010-06-06, and nothing in the interfaces tells 302/304/307 that the populated start is later than the constant. The project's explicit-failure rule ("never use silent fallback values") rules out an implicit fall back to `trading_calendars` hours. State the behavior (an explicit error naming the calendar and date is the natural fit), add it to the era-lookup unit tests (the listed "before, on and after a change" case covers era boundaries, not the pre-first-era case), and state how the supported range is recorded if the fill stops early.

### [CONCERN] Unbounded era-entry rule conflicts with the "2020 onward untouched" guarantee

D2 bounds exception rows at 2019-12-31, but D1's era rule ("Eras are entered only from dated CME documents", line 107) has no upper bound. If sourcing surfaces a documented regular-hours change on or after 2020-01-01, the design's rules pull apart: entering it makes `trading_calendar_hours` assert hours for 2020+ dates that the stored sessions (seeded from the `trading_calendars` row and declared byte-identical, lines 33 and 157) do not follow; not entering it leaves the era table knowingly wrong for that period. The migration's before/after guard (D3) cannot catch either way out: step 4 fills only `[CME_DATASET_START, SEED_START)`, so the 2020+ rows it compares are never rewritten and pass trivially, while a later rebuild from the tables — the repair paths D1 relies on — would regenerate them differently from the verified state. Success criterion 5 (line 160) is worded over any era change, so it would silently fail for a post-2020 boundary. State the resolution rule explicitly, e.g. era boundaries are searched and entered only for 2010-06-06 to 2019-12-31, and a documented later change is recorded and raised as a finding rather than entered.

### [NOTE] `ON CONFLICT DO NOTHING` keeps a differing pre-existing holiday row silently

Steps 3 and the schema section (also line 133) insert the pre-2020 exception rows with `ON CONFLICT DO NOTHING`. If a conflicting row already exists with different contents, the migration silently keeps it and sessions are built from it; the source test (Sources, line 137) checks seed-module data, not database state. Risk is low today (the calendars hold nothing before 2020-01-01, so first-run conflicts cannot occur), but the project's explicit-failure convention would prefer a conflict path that fails the migration when a stored row differs from the seed — or a short note that conflict-free first-run is a checked precondition.

### [NOTE] Slice 305 is missing from the provided-interfaces list

The frontmatter `interfaces` (line 7) and "Provides to Other Slices" list 302, 303, 304, 306 and 307 but not 305 (Futures Completeness Surfaces). 305's per-session completeness verdicts over the 2010–2019 range read exactly the pre-2020 sessions this slice creates (plan entry 305: verdicts per session against the CME calendar). The dependency is transitive through 302/303 so nothing is blocked, but 306 is listed for the same class of read; naming 305 alongside it would make the interface list complete.

### [NOTE] Parent architecture states the session shape as one fixed window

"What a 'day' is" describes the CME session as running "through the daily maintenance break (17:00–18:00 ET)" as a single fixed window. D1 (slice line 107) establishes that the close moved — 16:15 CT before about September 2015 versus 16:00 CT after. The two documents agree for 2020+, but the parent's undated wording, read over the 2010+ range this initiative serves, could mislead later slices (306's roll-up clipping, the `1d` bar definition) into assuming one constant break window. A one-line amendment to the parent, or an explicit note in this slice that the parent's window is the current era only, would close the gap.

### Run Digest

- Response length: 7767 chars
- Response is newline-free: no
- Tool calls made: 8
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 131072 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 114149
- Effort: backend default
- Turns: 6
- Tokens — prompt / cached / cache write / completion / reasoning: 148295 / 94208 / not computed / 31841 / 29249
- Duration: 716.2 s
- Cost: $0.05 (alias_pricing)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
