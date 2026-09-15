---
docType: review
layer: project
reviewType: code
slice: api-surface-coverage-operations-and-freshness
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/189-slice.api-surface-coverage-operations-and-freshness.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260915
dateUpdated: 20260915
reviewedSha: 11e70e82e8d246e8515f8d353687b962e06bd2fa
findings:
  - id: F001
    severity: concern
    category: resource-management
    summary: "Credit executor has no lifecycle management"
    location: "src/manta_trading/api_server/routes/operations.py:50"
  - id: F002
    severity: note
    category: architecture
    summary: "API server's core logic lives in the CLI package"
    location: "src/manta_trading/api_server/routes/operations.py:32"
  - id: F003
    severity: note
    category: duplication
    summary: "`api_key` truthiness guard duplicated between CLI and API route"
    location: "src/manta_trading/api_server/routes/operations.py#get_credits"
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Third-party credential redaction is real and tested"
    location: "src/manta_trading/api_server/routes/operations.py#get_credits"
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Pre-055 database degrades to 200, not 500, and the fixture is guarded"
    location: "test/integration/test_operations_serving.py:230"
---

# Review: code — slice 189

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] Credit executor has no lifecycle management

`_credit_executor` is a module-level `ThreadPoolExecutor` created at import time and never explicitly shut down. This is inconsistent with the project's own established pattern for shared resources in this file: `app.state.db_pool` is deliberately opened and closed inside `lifespan()` (per the comment in `app.py` — "the pool must be opened before the first request"), precisely because ad hoc resource lifetimes are considered a hazard. `_credit_executor` gets none of that discipline — it is a bare global, invisible to `create_app`/`lifespan`, and relies on Python's default `atexit` executor-shutdown behavior to join its worker threads. Because a stuck call can legitimately block for the full `EODHD_ACCOUNT_TIMEOUT_SECONDS` (that is the scenario the load test exercises), an in-flight credit fetch at process shutdown will delay interpreter exit by up to that timeout with no way for the app's own shutdown path to intervene or log it. Tying the executor's lifetime to `lifespan()` (create on startup, `shutdown(wait=False, cancel_futures=True)` on teardown) would make its lifecycle match the rest of the app's resources and avoid this uncontrolled shutdown delay.

### [NOTE] API server's core logic lives in the CLI package

`routes/operations.py` imports `build_overview` and `gather_db_facts` from `manta_trading.cli.commands.overview`, and the response models import `Overview`/`PassLine` from `manta_trading.cli.overview_types`. This makes the API server depend on the CLI package for its core read/derive logic, which is a layering inversion — normally a CLI is one client of a shared service/core layer, not the other way around. The choice is clearly deliberate (documented as 189 D3, with `TestTheSplitGuard` pinning that the two readers can't drift), so it's not a correctness problem today, but as more routes need this logic it will keep pulling `api_server` deeper into `cli.commands.*`. Worth watching whether `gather_db_facts`/`build_overview`/`overview_types` should eventually move to a neutral module both `cli` and `api_server` import from, rather than API code reaching into `cli.commands`.

### [NOTE] `api_key` truthiness guard duplicated between CLI and API route

`get_credits` re-implements the `if not api_key: ...CREDITS_NO_KEY` guard that already exists in `cli/commands/overview.py::gather`. Both branches do produce/share the same `CREDITS_NO_KEY` constant (good — the value itself isn't duplicated per CLAUDE.md's "single source" rule), but the conditional logic itself is repeated in two places. Minor, given it's a one-line guard, but consistent with the general theme above: as this surface grows, a shared `resolve_credits(settings) -> CreditUsage | (None, str)` helper reused by both the CLI and the route would remove the duplication entirely.

### [PASS] Third-party credential redaction is real and tested

The `except Exception` around the outbound credit fetch is broad, but it's scoped narrowly to the one HTTP call, documented as a deliberate top-level boundary handler (satisfying CLAUDE.md's exception-handling case (c)), logged with `exc_info=True`, and — critically — the message is redacted via `redact_token` before it reaches either the logger or the response body. `test_the_api_token_never_reaches_the_body` in `test/unit/api_server/test_operations.py` exercises this against a message shaped exactly like the real `httpx` exception text (`api_token=` in the query string), not a synthetic one, so the test actually proves the credential can't leak rather than just asserting the code path was hit.

### [PASS] Pre-055 database degrades to 200, not 500, and the fixture is guarded

`TestPre055Degradation` proves the SC4 requirement (database predating the `pass_runs` migration must not 500) against a real dropped-table fixture, and — importantly — `test_the_table_really_is_absent` guards the fixture itself so a passing test can't be hiding a fixture that silently kept the table. The destructive `DROP TABLE ... CASCADE` is scoped to the UUID-named throwaway `ephemeral_db`, consistent with the project's production-database-protection rules.
