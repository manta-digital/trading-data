---
docType: review
layer: project
reviewType: code
slice: universe-expansion-gc
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/232-slice.universe-expansion-gc.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261014
dateUpdated: 20261014
reviewedSha: 8b7b6ccec549dad89b3053f7e616818bec228fe0
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 0
diffTruncated: false
durationSeconds: 21.8
squadronVersion: 0.21.7
findings:
  - id: F001
    severity: concern
    category: design
    summary: "Seed module imports private helpers from a sibling module"
    location: "src/manta_trading/market/schema/seed_cme_metals_calendar.py:32-36"
  - id: F002
    severity: concern
    category: testing
    summary: "Provider-equivalence test now tolerates known divergences and filters sessions"
    location: "test/unit/data/tick/test_roll_provider_equivalence.py:30-40"
  - id: F003
    severity: concern
    category: conventions
    summary: "Hard-coded calendar id and bound in tests instead of the constants"
    location: "test/integration/test_migration_057_058_cme_calendar.py:250"
  - id: F004
    severity: note
    category: correctness
    summary: "`raw_counts` now excludes trades that lack a definition row"
    location: "src/manta_trading/data/tick/status_reads.py:204-209"
  - id: F005
    severity: note
    category: determinism
    summary: "Migration 059 depends on the wall-clock year"
    location: "src/manta_trading/market/schema/migrations/minute.py:322"
  - id: F006
    severity: note
    category: testing
    summary: "Recorded GC fixtures are not in the reviewed diff"
    location: "test/tick_support/roll_fixture.py#recorded_inputs"
  - id: F007
    severity: pass
    category: design
    summary: "Calendar seed refactor and universe changes"
    location: "src/manta_trading/market/schema/seed_cme_calendar.py#CmeCalendarSeed"
---

# Review: code — slice 232

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Seed module imports private helpers from a sibling module

`seed_cme_metals_calendar.py` imports `_closed` and `_early_close`, which are underscore-private in `seed_cme_calendar.py`. It also builds a ~390-line table from them. This couples two modules through names the owner has marked as internal. The module is also ~390 lines, over the ~300-line guideline. The data table is arguably exempt, but the private coupling is avoidable. Rename the helpers to public names, or move them with `CmeCalendarSeed` into a small shared module.

### [CONCERN] Provider-equivalence test now tolerates known divergences and filters sessions

The test is the pin on `VOLUME_RANK_LAG_SESSIONS`. It now accepts three GC `v.1` mismatches through `KNOWN_DIVERGENCE`. It also drops sessions before `_held_from(product)` and loosens `> 40` to `>= 40`. The set is exact, so any change fails the test, and the comment records the PM acceptance. The weakening is real, though. A future lag regression that only affects `GC.v.1` would be absorbed silently if it lands on those same dates. Consider an `xfail(strict=True)` or a separate test per known divergence, so the divergence is visible in test output rather than hidden inside an equality assertion.

### [CONCERN] Hard-coded calendar id and bound in tests instead of the constants

The test hard-codes `"CME_METALS": "2027-12-31"`. `CME_METALS_CALENDAR_ID` and `CME_METALS_HOLIDAYS_SEEDED_THROUGH` already exist. `test_migration_059_cme_metals_calendar.py` also repeats `date(2027, 12, 31)` and `"2027-12-31"`. The project rule says a value used in comparisons should be defined once. Changing the bound when CME publishes 2028 would require edits in several places. Reference the constants instead.

### [NOTE] `raw_counts` now excludes trades that lack a definition row

The new `instrument_id IN (SELECT ... FROM tick_definition WHERE asset = %s)` filter fixes cross-product overcounting and is parameterized. It silently drops any trade whose definition is missing. The docstring justifies this by saying ingest never stores a trade without its definition. That invariant is not enforced in the query. If it breaks, the raw count under-reports without any error, which sits close to the project's "no silent fallbacks" principle. The test covers the per-product split, but not a trade with no definition. I did not check whether `tick_definition` is indexed on `asset`. The subquery runs once per status or coverage build, so this is probably fine.

### [NOTE] Migration 059 depends on the wall-clock year

`end_year = datetime.now().year + TRADING_SESSIONS_EXTENSION_YEARS` follows 058's pattern, and the extension is clamped to the holiday bound. The integration test `test_sessions_span_seed_start_to_bound` asserts `last == CME_METALS_HOLIDAYS_SEEDED_THROUGH`. That assertion only holds while the current year plus the extension reaches 2027. It is fine today (2026) and consistent with 058, but it is time-dependent.

### [NOTE] Recorded GC fixtures are not in the reviewed diff

`recorded_inputs("GC")` and `provider_intervals("GC")` read `gc_{definitions,volumes,calendar,databento}.json`. Those fixtures are excluded from the scoped diff (JSON), so I could not verify them. The equivalence tests will fail loudly if they are missing, which is the right behavior.

### [PASS] Calendar seed refactor and universe changes

The frozen `CmeCalendarSeed` dataclass removes duplication between the equity and metals builders. The equity wrappers are kept as thin delegators, and a test confirms they match the shared builders. The `TICK_UNIVERSE`, `FUTURES_PRODUCT_CALENDAR`, migration list and chain-tip tests are updated consistently. The ES-only pinning (`two_products`, `use_only`, the synthetic `ZZ` product) isolates existing tests from the new GC entry. The record script's `--product` option is validated against the universe through `choices`, so the `next(...)` lookup cannot miss.

### Run Digest

- Response length: 4591 chars
- Response is newline-free: no
- Tool calls made: 0
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 21.8 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
