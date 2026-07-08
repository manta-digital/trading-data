---
docType: review
layer: project
reviewType: tasks
slice: universe-rebuild-from-eodhd-instruments-schema-migration
project: squadron
verdict: UNKNOWN
sourceDocument: project-documents/user/tasks/141-tasks.universe-rebuild-from-eodhd-instruments-schema-migration.md
aiModel: moonshotai/kimi-k2.6
status: complete
dateCreated: 20260430
dateUpdated: 20260430
findings:
  - id: F001
    severity: fail
    category: sequencing
    summary: "Migration 017 applied before consumer code is updated"
    location: project-documents/user/tasks/141-tasks.universe-rebuild-from-eodhd-instruments-schema-migration.md
  - id: F002
    severity: concern
    category: gap
    summary: "Missing Finnhub pre-flight in rebuild orchestrator"
    location: project-documents/user/tasks/141-tasks.universe-rebuild-from-eodhd-instruments-schema-migration.md
  - id: F003
    severity: concern
    category: scoping
    summary: "Task 9 orchestrator is too large and should be split"
    location: project-documents/user/tasks/141-tasks.universe-rebuild-from-eodhd-instruments-schema-migration.md
  - id: F004
    severity: concern
    category: architecture
    summary: "Architecture deviation: missing `finnhub_ipo_client.py` and `rate_limit.py` modules"
    location: project-documents/user/tasks/141-tasks.universe-rebuild-from-eodhd-instruments-schema-migration.md
  - id: F005
    severity: concern
    category: implementation
    summary: "Rate limiting implemented as `asyncio.Semaphore` instead of token bucket"
    location: project-documents/user/tasks/141-tasks.universe-rebuild-from-eodhd-instruments-schema-migration.md
  - id: F006
    severity: concern
    category: process
    summary: "Lint and type checks batched at end instead of per-module"
    location: project-documents/user/tasks/141-tasks.universe-rebuild-from-eodhd-instruments-schema-migration.md
  - id: F007
    severity: concern
    category: documentation
    summary: "Task 3.2 references incorrect downstream task number"
    location: project-documents/user/tasks/141-tasks.universe-rebuild-from-eodhd-instruments-schema-migration.md
  - id: F008
    severity: concern
    category: gap
    summary: "Missing explicit test for `--json` CLI output"
    location: project-documents/user/tasks/141-tasks.universe-rebuild-from-eodhd-instruments-schema-migration.md
  - id: F009
    severity: concern
    category: gap
    summary: "Orchestrator task does not distinguish INDX rows in canonical_id/venue derivation"
    location: project-documents/user/tasks/141-tasks.universe-rebuild-from-eodhd-instruments-schema-migration.md
  - id: F010
    severity: concern
    category: gap
    summary: "`--symbol` lookup success criterion lacks explicit test"
    location: project-documents/user/tasks/141-tasks.universe-rebuild-from-eodhd-instruments-schema-migration.md
  - id: F011
    severity: concern
    category: gap
    summary: "Criterion 11 (disappearing symbols between runs) is not explicitly tested"
    location: project-documents/user/tasks/141-tasks.universe-rebuild-from-eodhd-instruments-schema-migration.md
  - id: F012
    severity: pass
    category: functional-coverage
    summary: "AV-seeded venue preservation is well covered"
    location: project-documents/user/tasks/141-tasks.universe-rebuild-from-eodhd-instruments-schema-migration.md
  - id: F013
    severity: pass
    category: functional-coverage
    summary: "Residual `venue='US'` quantification is included"
    location: project-documents/user/tasks/141-tasks.universe-rebuild-from-eodhd-instruments-schema-migration.md
  - id: F014
    severity: pass
    category: functional-coverage
    summary: "EODHD and Finnhub failure modes are tested"
    location: project-documents/user/tasks/141-tasks.universe-rebuild-from-eodhd-instruments-schema-migration.md
---

# Review: tasks — slice 141

**Verdict:** UNKNOWN
**Model:** moonshotai/kimi-k2.6

## Findings

### [FAIL] Migration 017 applied before consumer code is updated

Task 9 applies migration 017 (`DROP active`) as part of the orchestrator, but Task 11 — which removes `active` from `Instrument`, `_INSTRUMENT_COLS`, and all query predicates — is scheduled afterwards. Between these tasks the database lacks the `active` column while the codebase still references it, guaranteeing runtime SQL errors. The slice design’s implementation notes explicitly order consumer updates *before* migration 017. This also means the integration test in Task 9.3 runs against a schema that is incompatible with the still-unupdated `InstrumentRegistry`.

### [CONCERN] Missing Finnhub pre-flight in rebuild orchestrator

The slice design’s D9 mandates two pre-flights: a fatal EODHD pre-flight and a non-fatal Finnhub pre-flight (`/stock/profile2?symbol=AAPL`). Task 9.1 only lists the EODHD pre-flight. Omitting the Finnhub pre-flight removes the early “warn-and-degrade” signal defined in D9, so the operator gets no early warning of a bad Finnhub key before the 17-hour enrichment loop (or its smoke-test equivalent) begins.

### [CONCERN] Task 9 orchestrator is too large and should be split

Task 9 bundles the full 9-step orchestrator implementation, migration entries 016/017, a seven-part integration test suite, and a commit checkpoint into a single task. For a junior AI this is too large to complete independently in one pass. It should be split into at least: (a) orchestrator core implementation, (b) migrations 016/017 registration, and (c) integration tests.

### [CONCERN] Architecture deviation: missing `finnhub_ipo_client.py` and `rate_limit.py` modules

The slice design’s component diagram places `data/universe/finnhub_ipo_client.py` between the thin HTTP client and the orchestrator, and D8 specifies per-provider `rate_limit.py` token-bucket modules. The task breakdown collapses both responsibilities into the `api/finnhub/finnhubapi.py` and `data/universe/eodhd_symbol_list_client.py` classes without an explicit decision to deviate from the documented component structure.

### [CONCERN] Rate limiting implemented as `asyncio.Semaphore` instead of token bucket

D8 requires a token-bucket limiter at 60/min for Finnhub. Task 8.2 states “`asyncio.Semaphore` at 60/min”. A semaphore limits concurrent requests, not requests-per-minute over time, so it does not satisfy the token-bucket specification and could burst past Finnhub’s free-tier limit.

### [CONCERN] Lint and type checks batched at end instead of per-module

Task 11.5 runs `ruff check src/` and `pyright --strict src/` only after all other implementation is done. The technical requirements state that all new modules must pass these checks. Batching them at the end risks intermediate commits (e.g., Task 4.4, 5.3, 7.3, 8.4) being accepted without lint/type-cleanliness. These checks should be included in each implementation task before its commit.

### [CONCERN] Task 3.2 references incorrect downstream task number

Task 3.2 says “Do NOT remove `active` from `Instrument` or queries yet — that is Task 10.” The removal of `active` is actually defined in Task 11 (Task 10 is the CLI surface). This typo could mislead a junior AI about when to perform the cleanup.

### [CONCERN] Missing explicit test for `--json` CLI output

Task 10.1 adds a `--json` flag to `mt data instruments rebuild`. Neither the smoke tests (Task 10.3) nor the integration tests (Task 9.3) assert that `--json` emits valid JSON or contains the expected canonical keys. The verification walkthrough (Task 12) also omits it, leaving the `--json` contract untested.

### [CONCERN] Orchestrator task does not distinguish INDX rows in canonical_id/venue derivation

D4 specifies that index rows from the INDX list receive `venue = 'INDX'` and `canonical_id = '{symbol}.INDX'`. Task 9.1 step 4 says all new rows get transient `venue='US'`/`canonical_id='{symbol}.US'`, with no carve-out for indices. This will incorrectly assign `US` placeholders to index rows, which the design explicitly says should retain `INDX` and not be promoted by Finnhub.

### [CONCERN] `--symbol` lookup success criterion lacks explicit test

Success criterion 7 requires that `--symbol AAPL` (or `get_by_symbol('AAPL')`) returns the row without the operator needing the canonical_id. Task 12.2 verifies AAPL’s fields via raw SQL, but no task explicitly tests `InstrumentRegistry.get_by_symbol('AAPL')` or any CLI `--symbol` path, leaving the operator-facing lookup contract unverified.

### [CONCERN] Criterion 11 (disappearing symbols between runs) is not explicitly tested

Success criterion 11 states that a symbol present in EODHD on run 1 but absent on run 2 should be deleted as an orphan on run 2. Task 9.3 tests AV orphan deletion via `eodhd_type IS NULL`, but does not simulate a symbol that was previously EODHD-populated then disappearing. Because the upsert mechanism only touches rows present in the current payload, a previously-populated symbol would retain its `eodhd_type` and would not be caught by the `eodhd_type IS NULL` orphan filter. An explicit test for this scenario is missing.

### [PASS] AV-seeded venue preservation is well covered

Task 9.3c requires a snapshot diff proving AV-seeded (symbol, venue, canonical_id) tuples survive the rebuild unchanged, and Task 12.2 explicitly confirms AAPL/MSFT/GE/JPM venues and canonical_ids. This directly satisfies success criterion 6.

### [PASS] Residual `venue='US'` quantification is included

Task 12.3 explicitly records the residual `venue='US'` count after a Finnhub-enabled run, satisfying success criterion 13.

### [PASS] EODHD and Finnhub failure modes are tested

Task 9.3f asserts that EODHD 403 halts with non-zero exit and no DB mutation (criterion 8). Task 9.3g asserts that Finnhub 403 does not halt, EODHD upsert plus migrations 015/016/017 complete, and exit code is 0 (criterion 9). Both critical failure paths have integration-test coverage.
