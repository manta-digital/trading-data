---
docType: review
layer: project
reviewType: code
slice: kalshi-provider-foundation
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/261-slice.kalshi-provider-foundation.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260824
dateUpdated: 20260824
reviewedSha: 1f982af87b65d06acf2a3aee45e3779590fdc1f4
findings:
  - id: F001
    severity: concern
    category: async-correctness
    summary: "RSA-PSS request signing runs synchronously inside an async function"
    location: "src/manta_trading/data/kalshi/transport.py:1350"
  - id: F002
    severity: concern
    category: consistency
    summary: "Migration's \"mechanical mapping\" claim doesn't match the actual model/schema field sets"
    location: "src/manta_trading/market/schema/migrations/kalshi.py:1617"
  - id: F003
    severity: pass
    category: correctness
    summary: "Error taxonomy, retries, and URL/path-signing logic are correct"
    location: "src/manta_trading/data/kalshi/transport.py:1440"
---

# Review: code — slice 261

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] RSA-PSS request signing runs synchronously inside an async function

`KalshiTransport.get_json` (async) calls `self.request_headers("GET", path)` inline, which for authenticated mode calls `KalshiCredentials.sign()` — a synchronous RSA-2048 PSS signature computed directly on the event-loop thread. I benchmarked this at ~0.6ms average per signature on unloaded dev hardware (verified via `cryptography`'s `sign()`), which is close to — and offers no margin under — the project's explicit rule in `python.md`: *"Any async def function that calls synchronous code must guarantee that the synchronous code runs in <1ms in the worst case... Anything CPU-bound must use run_in_executor, a dedicated thread, or a subprocess... Reviewers MUST verify this."* This isn't a hypothetical: the project already offloads much cheaper sync work to executors elsewhere (`api_server/routes/*.py`, `orchestrator.py`). Under retries (up to `KALSHI_MAX_RETRIES + 1` signs per logical call) or if this client shares a loop with other I/O, the accumulated blocking is real. Suggest wrapping the sign call in `asyncio.to_thread` or `loop.run_in_executor`.

### [CONCERN] Migration's "mechanical mapping" claim doesn't match the actual model/schema field sets

The `kalshi_002_catalog` migration comment asserts column names "follow the API field names verbatim... so the raw→column mapping in 262's upsert is mechanical." Comparing against `models.py`, the `Market` model has `previous_yes_bid_dollars`, `previous_yes_ask_dollars`, `yes_bid_size_fp`, `yes_ask_size_fp` with no matching table column, while `kalshi.markets` has `strike_type`, `price_level_structure`, `is_provisional`, `mve_collection_ticker` with no matching model field (same pattern on `series`: `contract_url`/`contract_terms_url`; `events`: `available_on_brokers`/`settlement_sources`). Data isn't lost today (raw JSONB + `extra="allow"` catch everything), but a literally "mechanical" field→column upsert in slice 262 would silently miss four price/size columns and four schema columns. Worth reconciling the model and schema (or softening the docstring's claim) before 262 builds on top of it.

### [PASS] Error taxonomy, retries, and URL/path-signing logic are correct

Independently verified: httpx's `base_url` + absolute-path join produces the expected `/trade-api/v2/...` URLs (no double-prefix bug), `httpx.DecodingError` is confirmed *not* a `TransportError` subclass so it correctly falls into the permanent (no-retry) path matching the test's expectation, and the full new test suite (123 tests), ruff, and mypy all pass clean.
