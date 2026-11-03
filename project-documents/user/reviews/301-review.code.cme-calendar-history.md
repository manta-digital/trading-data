---
docType: review
layer: project
reviewType: code
slice: cme-calendar-history
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/301-slice.cme-calendar-history.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261103
dateUpdated: 20261103
reviewedSha: e5afd713562a1a4e8a4183306f2a791df738293c
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 1
diffTruncated: false
turns: 2
promptTokens: 254643
cachedTokens: 16579
cacheWriteTokens: 238060
completionTokens: 3689
durationSeconds: 34.7
costUsd: 0.999283
costBasis: provider
squadronVersion: 0.24.11
categoriesNormalized: true
findings:
  - id: F001
    severity: concern
    category: error-handling-observability
    summary: "Cutover leaves timers stopped if `hold_timers` fails partway"
    location: "scripts/cutover_301_calendar_history.py:131-135"
  - id: F002
    severity: concern
    category: type-contract-debt
    summary: "Migration helpers use an untyped `conn: Any` and unchecked `fetchone()[0]`"
    location: "src/manta_trading/market/schema/migrations/cme_calendar_history.py:121-130"
  - id: F003
    severity: concern
    category: test-debt
    summary: "Count-check script has no tests for its window and finding logic"
    location: "scripts/check_cme_history_counts.py:97-125"
  - id: F004
    severity: note
    category: architectural-decay
    summary: "Seed data files far exceed the ~300-line guideline"
    location: "src/manta_trading/market/schema/seed_cme_equity_history.py"
  - id: F005
    severity: note
    category: correctness
    summary: "Era segmentation, the 066 safety checks and their tests are sound"
    location: "src/manta_trading/data/base/session_eras.py"
---

# Review: code — slice 301

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Cutover leaves timers stopped if `hold_timers` fails partway

`held` is only assigned after `hold_timers()` returns. If it raises after stopping some timers, `held` is still `[]`. The `finally` block then calls `release_timers([])`, and the timers that were stopped stay stopped. The failure message says nothing about it. Neither unit test covers this path. `hold_timers` should record what it stopped before it can raise, or the script should release by querying unit state. At minimum the failure message should name the timers that are still stopped.

### [CONCERN] Migration helpers use an untyped `conn: Any` and unchecked `fetchone()[0]`

`_refuse_existing_history` calls `cur.fetchone()[0]`, and `fetchone()` is typed as optional. The `conn: Any` annotations on the helpers hide this from pyright. The repo's strict pyright config would flag it if the connection were typed as `psycopg.Connection[Any]`, as in `session_eras.py`. A `count(*)` query always returns a row, so this is unlikely to fail at runtime. It is still a typing gap in a one-shot, rollback-on-failure migration.

### [CONCERN] Count-check script has no tests for its window and finding logic

The script's own docstring calls it "a one-off record", and its output feeds the findings recorded for this slice. The logic that decides what counts as a finding is untested. That includes `_window` (`expect_trading == (count > 0)`), the `[*sessions[1:], None]` pairing, the lookahead session, and the exit codes. The cutover script gets unit tests; this one should get a small one too, using a stubbed `_count`. Failed calls are never counted as zero, which is correct, and refusals stop the run, also correct.

### [NOTE] Seed data files far exceed the ~300-line guideline

The equity seed is about 587 lines and the metals seed about 525. Both are pure sourced data, and each module docstring says so. The same long citation is repeated above every row. A per-document source constant, as `seed_cme_hours.py` does with `_SER_6465`, would shrink them and give one place to edit each citation. The tests check that every row cites a source, so this is optional.

### [NOTE] Era segmentation, the 066 safety checks and their tests are sound

- `plan_segments` cuts ranges cleanly at era boundaries and raises explicitly when no era applies. This follows the "no silent fallback" rule.
- `is_dated` guards against databases that predate the `hours_dated` column, and there is a test for it.
- Migration 066 refuses to run if pre-2020 rows exist, checks that the latest era matches `trading_calendars`, and aborts if any 2020-onward session changed. Each abort path has an integration test.
- The 058/059 snapshot test and the retargeted older tests keep earlier behaviour pinned.
- `cme_holidays_insert` only inserts holiday rows, so the history seed's `holidays_seeded_through=HISTORY_END` does not overwrite the calendar's horizon.

### Run Digest

- Response length: 3405 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 254643 / 16579 / 238060 / 3689 / not computed
- Duration: 34.7 s
- Cost: $1.00 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 5
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 5
- Finding-shaped matches — surviving validation: 5
