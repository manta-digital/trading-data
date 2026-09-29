---
docType: review
layer: project
reviewType: code
slice: tick-storage-track
targetKind: slice
rulesSource: project
project: trading-data
verdict: PASS
verdictSource: stated
sourceDocument: project-documents/user/slices/222-slice.tick-storage-track.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20260928
dateUpdated: 20260928
reviewedSha: bc48e4e15199a3511d0a8173fb75d3888d5b7eec
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 0
diffTruncated: false
squadronVersion: 0.15.1
findings:
  - id: F001
    severity: pass
    category: design
    summary: "Enum-derived CHECK constraints and idempotent migrations"
    location: "src/manta_trading/market/schema/migrations/tick.py:1-120"
  - id: F002
    severity: pass
    category: security
    summary: "Production-database safety in tests"
    location: "test/integration/data/test_tick_role_privileges.py"
  - id: F003
    severity: note
    category: design
    summary: "`_in_list` is knowingly duplicated from the Kalshi track"
    location: "src/manta_trading/market/schema/migrations/tick.py:38-45"
  - id: F004
    severity: note
    category: testing
    summary: "Tests import private helpers"
    location: "test/integration/data/test_tick_storage_track.py:33"
  - id: F005
    severity: note
    category: style
    summary: "Unused module-level names in the constraint tests"
    location: "test/integration/data/test_tick_storage_constraints.py:34-38"
  - id: F006
    severity: note
    category: performance
    summary: "Foreign keys without supporting indexes"
    location: "src/manta_trading/market/schema/migrations/tick.py#TICK_MIGRATIONS"
  - id: F007
    severity: note
    category: correctness
    summary: "Definition window bounds are NOT NULL"
    location: "src/manta_trading/market/schema/migrations/tick.py#tick_003_definitions"
---

# Review: code — slice 222

**Verdict:** PASS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Enum-derived CHECK constraints and idempotent migrations

Every enum CHECK is rendered from its enum through `_check_in` and `_in_list`, so each value is defined in one place. Constraints are named inside the `CREATE`, and the DDL uses `IF NOT EXISTS` and `if_not_exists => TRUE`. Unit tests cover the rendering helpers. Integration tests cover a second apply as a no-op, and both a passing and a rejected row for each constraint. `_interval_ns` uses integer arithmetic only, with no float rounding.

### [PASS] Production-database safety in tests

The new DML, TRUNCATE and ALTER probes run as `tick_app` against the fixture-created database. The DML probe is rolled back. The provisioning artifact grants DML only, with no TRUNCATE and no sequence grant. `test_artifact_enumerates_every_tick_table` stops a new table from being left out of the audited write surface.

### [NOTE] `_in_list` is knowingly duplicated from the Kalshi track

The docstring says the helper is copied because the tick track must not import from `data/kalshi`. That is a reasonable trade-off. A shared neutral helper in `market/schema` would remove the duplication without breaking the boundary.

### [NOTE] Tests import private helpers

The integration and unit tests import `_interval_ns`, `_in_list` and `_check_in`, which are private. If the tests are meant to pin these helpers, drop the underscore prefix. Otherwise the tests are coupled to internals.

### [NOTE] Unused module-level names in the constraint tests

`NOT_A_MEMBER`, `_DAY` and the `date` import are unused in this file. They look left over from the manifest test file. Ruff does not flag unused module-level assignments, so nothing catches them.

### [NOTE] Foreign keys without supporting indexes

`tick_definition.unit_id`, `tick_archive_unit.superseded_by_unit_id` and `tick_trade.unit_id` have no index. The `tick_trade` case is deliberate (TD8: no index beyond the key), but it means per-unit lookups or deletes, such as re-ingesting a unit, scan the whole hypertable. Slice 224 or 225 should confirm that access pattern is acceptable.

### [NOTE] Definition window bounds are NOT NULL

`activation_ns` and `expiration_ns` are NOT NULL, while TD3 says provider "undefined" values are stored as NULL. This is fine for CME futures. If a definition ever arrives with an undefined `activation` or `expiration` (for example a spot instrument), the writer must handle it explicitly instead of failing on an insert error. Slice 223 should cover this.

### Run Digest

- Response length: 3148 chars
- Response is newline-free: no
- Tool calls made: 0
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
