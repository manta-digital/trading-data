---
docType: review
layer: project
reviewType: code
slice: universe-rebuild-from-eodhd-instruments-schema-migration
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/slices/141-slice.universe-rebuild-from-eodhd-instruments-schema-migration.md
aiModel: moonshotai/kimi-k2.5
status: complete
dateCreated: 20260501
dateUpdated: 20260501
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "Broad exception catch lacks specific type justification"
    location: src/manta_trading/api/finnhub/finnhubapi.py#FinnhubClient.fetch_profile
  - id: F002
    severity: pass
    category: security
    summary: "Proper use of environment variables for secrets"
    location: scripts/capture_eodhd_fixture.py:25
  - id: F003
    severity: pass
    category: typing
    summary: "Modern Python type hint usage"
    location: src/manta_trading/api/finnhub/finnhubapi.py:1
  - id: F004
    severity: pass
    category: style
    summary: "Correct import ordering"
    location: src/manta_trading/api/finnhub/finnhubapi.py:7-14
  - id: F005
    severity: note
    category: database
    summary: "SQL migration files deleted"
    location: database/migrations/
  - id: F006
    severity: note
    category: review-limitation
    summary: "File content truncated"
    location: src/manta_trading/api/http_retry.py
---

# Review: code — slice 141

**Verdict:** CONCERNS
**Model:** moonshotai/kimi-k2.5

## Findings

### [CONCERN] Broad exception catch lacks specific type justification

The retry loop at the end of `fetch_profile` catches `Exception` broadly rather than specific httpx exception types (`httpx.ConnectError`, `httpx.TimeoutException`, `httpx.HTTPStatusError`, etc.). Per the error handling rules, `try/except` must catch specific exception types, and any broad exception handling requires an inline comment justifying why it is correct. The current implementation catches `Exception` to handle "any failure during request" but does not document which specific failure modes are expected (network, DNS, timeout, HTTP errors) or why this broad catch is necessary for the retry logic. Consider catching `httpx.HTTPError` as the base class for httpx exceptions, or explicitly listing expected exception types with a comment explaining the defensive catch strategy.

### [PASS] Proper use of environment variables for secrets

EODHD scripts correctly load API keys from environment variables (`MT_EODHD_API_KEY`) using `os.environ` after `load_dotenv()`, avoiding hardcoded credentials. The `probe_eodhd_adjustment.py` script additionally redacts the API key from logged URLs via `_redact()`, preventing accidental credential leakage in logs.

### [PASS] Modern Python type hint usage

The finnhub module uses modern Python typing patterns: `from __future__ import annotations`, union syntax with `|` (e.g., `dict[str, Any] | None`), and fully typed function signatures including return types. This aligns with the requirement to target Python 3.12+ and use built-in generic types rather than `typing.Dict` or `Optional`.

### [PASS] Correct import ordering

Imports are correctly grouped and ordered: standard library (`asyncio`, `time`, `typing`), third party (`httpx`), then local application imports (`manta_trading.api.http_retry`, `manta_trading.logging`). No wildcard imports are present.

### [NOTE] SQL migration files deleted

The diff removes 12 SQL migration/validation/seed files (migrations 025, 750, 760, 770, 780 and corresponding validation/rollback scripts). This appears to be an intentional cleanup or schema consolidation. Ensure that:
1. These deletions are intentional and documented in the migration strategy
2. Downstream environments have already migrated past these versions before the files are removed from version control
3. Any replacement migrations are present (not shown in this diff)

### [NOTE] File content truncated

The git diff output for `http_retry.py` was truncated at 100KB before completion. Only the module docstring and imports were visible for review; the retry logic implementation could not be evaluated against the error handling and async rules.
