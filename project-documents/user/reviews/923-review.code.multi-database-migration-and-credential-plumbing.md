---
docType: review
layer: project
reviewType: code
slice: multi-database-migration-and-credential-plumbing
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/923-slice.multi-database-migration-and-credential-plumbing.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260928
dateUpdated: 20260928
reviewedSha: d7ed7c53e20efa2ef2927bbdf875ebeebdd07648
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 10
findings:
  - id: F001
    severity: concern
    category: code-structure
    summary: "`cli/commands/data.py` keeps growing past the project's size guideline"
    location: "src/manta_trading/cli/commands/data.py"
  - id: F002
    severity: note
    category: maintainability
    summary: "`migrate_status` special-cases the primary database's error text"
    location: "src/manta_trading/cli/commands/data.py:283-297"
  - id: F003
    severity: pass
    category: correctness
    summary: "No-fallback credential resolution is enforced in one place and tested from both directions"
    location: "src/manta_trading/market/schema/databases.py:76-83"
  - id: F004
    severity: pass
    category: correctness
    summary: "Ledger misroute guard is data-driven and collision-checked"
    location: "src/manta_trading/market/schema/databases.py:99-157"
  - id: F005
    severity: pass
    category: security
    summary: "Test-tier production-URL guard extended correctly to the new tick variables"
    location: "test/_prod_url_guard.py"
---

# Review: code — slice 923

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] `cli/commands/data.py` keeps growing past the project's size guideline

The file is already ~4000 lines long (confirmed by direct read), 13x over CLAUDE.md's "~300 lines where practical" guideline. This diff adds ~90 more lines of new routing logic (`_routed_db`, `_apply_track`, `_resolve_url_or_exit`) directly into it rather than extracting them. The project already has an established convention for this exact situation — `cli/commands/_pass_run.py` exists specifically to hold command helper logic out of the main command file (imported at `src/manta_trading/cli/commands/data.py:16`, `make_pass_run_recorder`). The new `(Database, Credential)`-routing helpers added here (`src/manta_trading/cli/commands/data.py:389-428`) are a natural fit for a similar `_data_routing.py` (or colocated with `market/schema/databases.py`, which is already described as the module other layers should route through). Not a functional bug, but it compounds an already-flagged-worthy file-size violation instead of following the project's own precedent for splitting it.

### [NOTE] `migrate_status` special-cases the primary database's error text

For the primary database, the JSON error string stays hardcoded as `"URL not configured"` while every other database uses `f"{exc.env_var} not configured"` (derived from `DatabaseNotConfiguredError`). This is deliberate and documented in-line ("The primary keeps its pre-923 JSON text byte for byte") to avoid a breaking JSON-output change, so it's not a bug — just a permanent small asymmetry future readers should know is intentional rather than an oversight.

### [PASS] No-fallback credential resolution is enforced in one place and tested from both directions

`resolve_database_url` reads exactly one `Settings` field per `(Database, Credential)` pair with no fallback logic anywhere else in the change (`config/__init__.py`, `data.py`), matching the CLAUDE.md "never use silent fallback values" rule. Verified by `test/unit/market/schema/test_databases.py` (`test_tick_maintenance_never_falls_back_to_tick_application`, `test_tick_application_never_falls_back_to_primary`).

### [PASS] Ledger misroute guard is data-driven and collision-checked

`assert_ledger_belongs`/`foreign_ledger_ids` derive database ownership from the `TRACK_REGISTRY` rather than hardcoding track/database pairs elsewhere (satisfies the "never scatter comparison values" rule), and `test/unit/market/schema/test_track_registry.py:58-67` specifically guards the invariant the lookup dict depends on (no migration id reused across two different databases), which is exactly the kind of case that would silently corrupt `_database_by_migration_id()`.

### [PASS] Test-tier production-URL guard extended correctly to the new tick variables

Both `test/integration/test_integration_prod_url_guard.py` and `test/unit/test_unit_prod_url_guard.py` were updated in lockstep with the `assert_ratchet` signature change (now requires an explicit `needles` tuple), and a zero-tolerance ratchet (`frozenset()`) is added for the tick URL variables per D9, consistent with the mandatory "tests never read the production database URL variable" rule.

### Run Digest

- Response length: 4216 chars
- Response is newline-free: no
- Tool calls made: 10
- Tool calls failed: 0
- Stop reason: end_turn
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 5
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 5
- Finding-shaped matches — surviving validation: 5
