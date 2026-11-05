---
docType: review
layer: project
reviewType: code
slice: statistics-and-the-open-interest-roll-rule
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261105
dateUpdated: 20261105
reviewedSha: 8e536ad0cccd6555dbaf93d7cc00fa26c62a377d
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 1
diffTruncated: false
turns: 2
promptTokens: 408333
cachedTokens: 16579
cacheWriteTokens: 391750
completionTokens: 2571
durationSeconds: 26.6
costUsd: 1.675725
costBasis: provider
squadronVersion: 0.24.19
categoriesNormalized: true
findings:
  - id: F001
    severity: concern
    category: architectural-decay
    summary: "`StatisticsProjector.load` is oversized and mixes several responsibilities"
    location: "src/manta_trading/data/tick/statistics_projector.py:107"
  - id: F002
    severity: concern
    category: test-debt
    summary: "Test builds `RollInputs` through `__dict__` instead of `dataclasses.replace`"
    location: "test/unit/data/tick/test_roll_resolve_open_interest.py:112"
  - id: F003
    severity: concern
    category: test-debt
    summary: "Test uses `try/except/else` with a manual raise instead of `pytest.raises`"
    location: "test/integration/data/test_tick_statistics_volume.py (`test_only_a_volume_family_can_be_checked`)"
  - id: F004
    severity: concern
    category: architectural-decay
    summary: "Resolver internals are exposed across modules without a contract"
    location: "src/manta_trading/data/tick/roll_resolve_oi.py:26"
  - id: F005
    severity: note
    category: consistency-rot
    summary: "`provision_tick_roles.sql` comment exceeds the width of the surrounding lines"
    location: "scripts/provision_tick_roles.sql:171"
  - id: F006
    severity: note
    category: other
    summary: "Positive observations"
    location: "src/manta_trading/data/tick/statistics_reads.py"
---

# Review: code — slice 303

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] `StatisticsProjector.load` is oversized and mixes several responsibilities

`load` runs from line 107 to about line 170, roughly 60 lines. That is over the ~50-line project limit and the 20-line Python preference. It does decode timing, unit-day checks, resolution, session assignment, ledger accumulation, ordinal numbering, COPY writing, progress reporting and note building in one loop. Extract a per-batch helper, for example `_project_batch(...)`, and keep `load` as the loop and the final summary. `content_findings` in `statistics_status.py` is also near the limit and could be split the same way.

### [CONCERN] Test builds `RollInputs` through `__dict__` instead of `dataclasses.replace`

`run.__class__(**{**run.__dict__, "statistics_statuses": {}})` depends on the dataclass internals and will break if `RollInputs` gains slots or init-only fields. The rest of the diff already uses `dataclasses.replace` (in `active_contract.py`), so use it here too.

### [CONCERN] Test uses `try/except/else` with a manual raise instead of `pytest.raises`

The test catches `ValueError`, asserts on the message, and calls `raise AssertionError` in the `else` branch. The project testing rules say to use pytest idioms. `pytest.raises(ValueError, match="no traded volume")` inside the family loop does the same thing and fails with a clearer message.

### [CONCERN] Resolver internals are exposed across modules without a contract

`roll_resolve_oi` imports the private `_Resolver` class under `TYPE_CHECKING`. It reads the resolver's `sessions`, `inputs`, `opens`, `closes`, `contracts` and `interest` attributes directly. It also calls `pick` and `make_contract`, which this diff made public. The coupling is intentional and tested by the import-direction test, but a small Protocol or a public resolver class would remove the private-name import and the shared mutable attributes.

### [NOTE] `provision_tick_roles.sql` comment exceeds the width of the surrounding lines

The reflowed comment leaves one line (`before they exist. DML only: no TRUNCATE, and no sequence grant (identity columns draw`) longer than its neighbours. This is cosmetic.

### [NOTE] Positive observations

- All new SQL binds its values as parameters. The table and ledger names that are interpolated come from code constants.
- The new row tables and ledger are granted DML only, with no TRUNCATE, in the role-provisioning file.
- No new test reads a production database URL. The tests use throwaway-database fixtures.
- The `statistics` family is routed through the existing families, ledgers and validation, with tests added at each layer. Undefined provider values are stored as NULL rather than as DBN sentinels. Each failure mode raises a named check rather than a fallback value.
- I found no swallowed exceptions in the added source.

### Run Digest

- Response length: 3515 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 408333 / 16579 / 391750 / 2571 / not computed
- Duration: 26.6 s
- Cost: $1.68 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
