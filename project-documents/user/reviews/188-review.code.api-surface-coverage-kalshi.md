---
docType: review
layer: project
reviewType: code
slice: api-surface-coverage-kalshi
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/188-slice.api-surface-coverage-kalshi.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260913
dateUpdated: 20260913
reviewedSha: 3e29c2afc500d786b26ee925efd0eb9269e18d52
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "TradeRecord.from_row uses hardcoded positional indices"
    location: "src/manta_trading/api_server/models/kalshi_timeseries.py:158"
  - id: F002
    severity: concern
    category: simplification
    summary: "_admit_rows/_not_found duplicated across two route modules"
    location: "src/manta_trading/api_server/routes/kalshi_timeseries.py:52"
  - id: F003
    severity: note
    category: naming
    summary: "get_max_bars name no longer matches Kalshi catalog usage"
    location: "src/manta_trading/api_server/deps.py:52"
  - id: F004
    severity: note
    category: typing
    summary: "JSONB-backed fields typed Any instead of a narrower type"
    location: "src/manta_trading/api_server/models/kalshi_catalog.py:46"
---

# Review: code — slice 188

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] TradeRecord.from_row uses hardcoded positional indices

`TradeRecord.from_row` unpacks `TradeRow.values` by literal index (`values[0]`..`values[7]`), while the sibling `CandleRecord.from_row` in the same file deliberately re-nests by walking `CANDLE_COLUMNS` by name specifically to avoid positional fragility. `TradeRow.values` comes from `trade_repository.TRADE_COLUMNS` order (minus `market_ticker`); today that order happens to match the hardcoded indices, but nothing ties them together, and no test would catch a reorder (`test_kalshi_trades.py` checks the column-name *set*, not order; `test_kalshi_models.py`'s trade test also builds a positional tuple). A future column insertion/reorder in `TRADE_COLUMNS` would silently misassign fields with no error.

### [CONCERN] _admit_rows/_not_found duplicated across two route modules

`kalshi_catalog.py` and `kalshi_timeseries.py` each define their own near-identical `_admit_rows` (422 refusal) and `_not_found` (404) helpers, differing only in wording ("narrow the filter" vs "narrow start/end"). This is the kind of duplication `windows.py` and `serialization.py` were extracted in this same PR to avoid between the bars and Kalshi routes; worth consolidating similarly.

### [NOTE] get_max_bars name no longer matches Kalshi catalog usage

`get_max_bars`/`app.state.max_bars_per_request` now gates Kalshi series/events/markets list sizes too (per the deliberate shared-ceiling design documented in `constants.py`), but the name still reads as bars-specific at the Kalshi call sites, with no local comment pointing to the rationale.

### [NOTE] JSONB-backed fields typed Any instead of a narrower type

`tags`, `settlement_sources`, `product_metadata` are typed `Any`, which is pyright-strict-legal but leaves the OpenAPI schema and shape guarantees for these fields undocumented. Possibly an accepted tradeoff for pass-through JSON blobs — worth a short comment saying so, matching the style used for the Decimal fields nearby.


---

## Resolution (2026-09-15)

All four findings addressed. The published OpenAPI contract is **byte-identical**
before and after — 17 paths, no path entry changed, schemas identical — because
every fix was internal.

### F001 (concern) — positional indices. **Fixed; it was a real latent bug.**

`TradeRecord.from_row` now maps by walking `TRADE_VALUE_COLUMNS`, the same
name-based approach `CandleRecord.from_row` already used, with `strict=True` so
a length mismatch raises instead of dropping a column.

Four regression tests added, and **verified to fail against the old code**:
reverting to the positional version produces `AssertionError: assert 'ask' ==
'no'` — `taker_outcome_side` and `taker_book_side` silently transposed, exactly
the failure the finding predicted. The helper that builds test values does so
*by name*, so the test cannot bake in the assumption it is checking.

### F002 (concern) — duplicated helpers. **Fixed.**

`admit_rows`/`not_found` now live in `api_server/admission.py`, the same
extraction `windows.py` and `serialization.py` got. The only thing that
legitimately differed — the remedy text — is a parameter (`NARROW_FILTER` vs
`NARROW_WINDOW`), not a reason for two functions.

Wire messages verified unchanged against production:

    422 …over the 75,000 row limit; narrow start/end      (time series)
    422 …over the 75,000 row limit; narrow the filter     (catalog)
    404 Market 'NOSUCHMARKET' not found
    404 Series 'NOSUCHSERIES' not found

Nine tests pin both remedies, the inclusive ceiling boundary, and the 404
wording per resource.

### F003 (note) — `get_max_bars` name. **Comment added** at both Kalshi call
sites pointing to the shared-ceiling rationale: the setting name is
bars-specific, the ceiling it carries is not.

### F004 (note) — JSONB fields typed `Any`. **Comment added** stating the
tradeoff explicitly: Kalshi owns these shapes and changes them without notice,
so a narrower type would either reject a valid payload or go stale silently.
OpenAPI documenting them as untyped is the accepted cost.

### Verification

- Unit **3606 passed / 0 failed** (up 13 — the new regression tests)
- OpenAPI artifact regenerated; contract unchanged
- Live against production: trades return every field correctly, both 422
  wordings and both 404 wordings preserved
