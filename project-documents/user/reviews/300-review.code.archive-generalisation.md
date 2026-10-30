---
docType: review
layer: project
reviewType: code
slice: archive-generalisation
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: PASS
verdictSource: stated
sourceDocument: project-documents/user/slices/300-slice.archive-generalisation.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261030
dateUpdated: 20261030
reviewedSha: 4c4e8ab63084742ca6ae243e05f0f32f9bb66a30
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 0
diffTruncated: false
turns: 1
promptTokens: 81008
cachedTokens: 13376
cacheWriteTokens: 67630
completionTokens: 2455
durationSeconds: 22.4
costUsd: 0.302655
costBasis: provider
squadronVersion: 0.24.10
categoriesNormalized: true
findings:
  - id: F001
    severity: note
    category: type-contract-debt
    summary: "Projector hand-off is loosely typed"
    location: "src/manta_trading/data/tick/projector_types.py:41"
  - id: F002
    severity: note
    category: other
    summary: "Plan-inclusion design is sound and tested"
    location: "src/manta_trading/data/tick/plan_inclusion.py#is_plan_included"
  - id: F003
    severity: note
    category: consistency-rot
    summary: "Family-scoped supersession SQL"
    location: "src/manta_trading/data/tick/schema_families.py:62"
  - id: F004
    severity: note
    category: consistency-rot
    summary: "Unrelated reformatting in test_settings.py"
    location: "test/unit/test_settings.py:38"
---

# Review: code — slice 300

**Verdict:** PASS
**Model:** claude-sonnet-5-5

## Findings

### [NOTE] Projector hand-off is loosely typed

`Loaded.bookkeeping` is typed `object | None`. `TickTradeProjector.finish` recovers the ledger rows with `cast("list[LedgerRow]", ...)` (`tick_trade_projector.py`). The worker never reads the field, so this is safe today. But the type checker cannot catch a projector whose `load` and `finish` disagree. When 301 adds a second projector, consider making `Loaded` generic or giving each projector its own typed result. `load` in `tick_trade_projector.py` is also about 35 lines, over the 20-line preference. It was moved unchanged, so this is not a regression.

### [NOTE] Plan-inclusion design is sound and tested

- **One classifier:** a single pure function feeds the estimate, the purchase plan, adoption and unheld-job filtering. Row inclusion is written once (`is_plan_included` on insert, no default after the `tick_008` backfill). Re-submits reuse the stored value.
- **Held jobs:** `held_job_ids` reads every request row, so plan-included jobs are not counted as unheld spend.
- **Config:** the plan is read from `Settings`, not `os.environ`. An invalid value fails with an error that names the variable and lists the allowed values. There are no silent fallbacks.
- **Tests:** they cover boundary days, straddling ranges, naive datetimes, other datasets, and the guard sums.

### [NOTE] Family-scoped supersession SQL

`supersedes_sql` builds `CASE` expressions from code constants only, and a schema in no family yields NULL, so it never supersedes. This replaces `tier_rank_sql`, and the tests for the old function were removed along with it. It also reads `FAMILY_SCHEMAS` at call time, but `SCHEMA_FAMILY` is computed once at import. The monkeypatching test only checks the rendered SQL, which is acceptable. Registering a new family means editing `FAMILY_SCHEMAS`, and `SCHEMA_FAMILY` follows from it.

### [NOTE] Unrelated reformatting in test_settings.py

Several existing tests were collapsed from multi-line to single-line signatures. This is formatter churn unrelated to slice 300. A separate `style:` commit would keep the feature diff focused.

### Run Digest

- Response length: 2468 chars
- Response is newline-free: no
- Tool calls made: 0
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 1
- Tokens — prompt / cached / cache write / completion / reasoning: 81008 / 13376 / 67630 / 2455 / not computed
- Duration: 22.4 s
- Cost: $0.30 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 4
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 4
- Finding-shaped matches — surviving validation: 4
