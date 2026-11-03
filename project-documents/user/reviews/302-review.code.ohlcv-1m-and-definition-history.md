---
docType: review
layer: project
reviewType: code
slice: ohlcv-1m-and-definition-history
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/302-slice.ohlcv-1m-and-definition-history.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261103
dateUpdated: 20261103
reviewedSha: 3a5ac9a358a00434d5bead7ec53d353226d71805
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 1
diffTruncated: false
turns: 2
promptTokens: 299380
cachedTokens: 16579
cacheWriteTokens: 282797
completionTokens: 4315
durationSeconds: 42.6
costUsd: 1.229919
costBasis: provider
squadronVersion: 0.24.14
categoriesNormalized: true
findings:
  - id: F001
    severity: concern
    category: architectural-decay
    summary: "Bar projector duplicates the trade projector's load loop and unit queries"
    location: "src/manta_trading/data/tick/ohlcv_1m_projector.py:55-125"
  - id: F002
    severity: concern
    category: correctness
    summary: "`zip(..., strict=False)` in the bar row writer hides length mismatches"
    location: "src/manta_trading/data/tick/ohlcv_1m_projector.py:77"
  - id: F003
    severity: concern
    category: consistency-rot
    summary: "Coverage JSON shaping relies on a recursive key-stripping hack"
    location: "src/manta_trading/data/tick/tick_coverage.py:72-86, 98-106"
  - id: F004
    severity: concern
    category: consistency-rot
    summary: "`Shape.params` still emits a `tiers` key that now holds any family's schemas"
    location: "src/manta_trading/data/tick/status_reads.py:61-68"
  - id: F005
    severity: concern
    category: test-debt
    summary: "Row-writing path for bars has no load test"
    location: "src/manta_trading/data/tick/ohlcv_1m_projector.py#Ohlcv1mProjector"
  - id: F006
    severity: note
    category: consistency-rot
    summary: "Test imports a private function"
    location: "test/integration/data/test_tick_status_build.py:34"
  - id: F007
    severity: note
    category: documentation-drift
    summary: "Fixture derivation script is correct but dense"
    location: "scripts/cut_tick_fixtures.py:139-169"
  - id: F008
    severity: pass
    category: other
    summary: "Migration, privileges, drill and projector wiring are consistent"
    location: "src/manta_trading/market/schema/migrations/tick.py:386"
---

# Review: code — slice 302

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Bar projector duplicates the trade projector's load loop and unit queries

`Ohlcv1mProjector.load`, `stored_count` and `delete_unit` mirror `TickTradeProjector`. They share the same decode-timing loop, `COPY` setup, batch bookkeeping, and `unit_id` plus `ts_event BETWEEN` count and delete statements. The docstring defers extraction to the `ohlcv-1s` slice. This slice already extracted `write_ledger` and `Loaded.note_batch`, so the remaining shared pieces could follow. The `load` body is about 35 lines, which is within the 50-line limit but long for a method with this much branching. Extract the shared pieces now, or record the deferral as a tracked issue.

### [CONCERN] `zip(..., strict=False)` in the bar row writer hides length mismatches

`zip(*columns, repeat(plan.unit.unit_id), strict=False)` is needed because `repeat` is infinite. If the column lists ever diverged, the rows would be silently truncated. The ledger and `decoded` counts come from `batch.count`, so the later provider = decoded = stored check would only catch it after the fact. Use `itertools.repeat(unit_id, batch.count)` with `strict=True`, or build the rows with an explicit length assertion.

### [CONCERN] Coverage JSON shaping relies on a recursive key-stripping hack

`to_dict` builds `asdict`, pops `schema`, re-adds it, and then `_without_bar_only` recursively deletes any `ledger_volume` or `raw_volume` key whose value is `None`. The goal is to keep the tick-tier output byte-identical to the old one. The filter is generic over every dict and list in the payload, so it could drop an unrelated key with the same name. Mixing optional bar-only fields into `Mismatch` and `CoverageSession` also pushes a "bars only" `None` convention through the types. A separate `BarCoverageSession` and `BarMismatch` subclass, or an explicit per-class `to_dict`, would be clearer. The golden-file test does protect the behaviour as it stands.

### [CONCERN] `Shape.params` still emits a `tiers` key that now holds any family's schemas

`SHAPE_WHERE` and the superseded query still bind `%(tiers)s`, but with this change it carries the schemas of whichever family was passed, including `ohlcv-1m`. Rename the parameter to `schemas` in `SHAPE_WHERE` and `params`, so the name matches what it holds. Related: `_SUPERSEDED` is now built with `str.format`, and the Python rules prefer f-strings. The inputs are code constants, so this is a style point, not a security one.

### [CONCERN] Row-writing path for bars has no load test

The projector writes each bar through `copy.write_row` from Python lists, inside the ingest worker. The Python testing rules require a `tests/load/` test with latency or throughput bounds for network, concurrency and simulation paths. The changed tests cover correctness only: e2e on one day, pre-2020, coverage and the migration. A pre-2020 backfill is the intended use of this projector. A load test over a realistic multi-instrument day with a throughput bound would catch regressions that unit and integration tests cannot.

### [NOTE] Test imports a private function

The test imports `_product_lines` from `tick_status_render`. A small public render function, or the existing `print_status`, would avoid coupling the test to a private helper.

### [NOTE] Fixture derivation script is correct but dense

`_derive_bars` relies on `np.lexsort` being stable and on `np.diff(..., prepend=-1)` of two keys OR-ed together to find group starts. Both are right: the OR of two integer diffs is non-zero when either is. The reason for the stability dependence is explained in a comment. A one-line comment on the OR would help the next reader. The docstring correctly says the fixture makes no provider-parity claim.

### [PASS] Migration, privileges, drill and projector wiring are consistent

- **Migration:** `tick_009` is idempotent and renders the CHECK from the enum. It reuses the `tick_007` columnstore layout.
- **Constants:** all table names and columns live in `bar_constants.py`, with no scattered literals.
- **Privileges:** `provision_tick_roles.sql` grants DML only and no TRUNCATE.
- **Role-privilege test:** it covers both new tables.
- **Drill scripts:** the 228 drill scripts take the new tables into account through `FINGERPRINTED_KEYS` and `BAR_LEDGER`.
- **DB safety:** the new tests use throwaway databases, and the one `DELETE` in a test targets a fixture-created database with a docstring explaining why.

### Run Digest

- Response length: 5644 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 299380 / 16579 / 282797 / 4315 / not computed
- Duration: 42.6 s
- Cost: $1.23 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
