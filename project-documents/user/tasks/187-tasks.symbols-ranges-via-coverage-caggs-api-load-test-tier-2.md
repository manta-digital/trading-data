---
docType: tasks
slice: symbols-ranges-via-coverage-caggs-api-load-test-tier
project: trading-data
lld: user/slices/187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier.md
dependencies: [167, 185, 186]
projectState: >
  Continues from
  187-tasks.symbols-ranges-via-coverage-caggs-api-load-test-tier-1.md (Tasks
  1-9): the coverage content-edge freshness check, the create_app(db_url=...)
  seam, fetch_available_ranges (universe edges, coverage, bounded head probe,
  merge), and the get_symbol rewire are implemented and unit-tested. Remaining
  work is the integration test against real caggs, the test/load/ tier for
  api_server, the D11 pool decision, document corrections, prod verification,
  and merge.
dateCreated: 20260804
dateUpdated: 20260804
status: not_started
---

## Context Summary

- **This is file 2 of 2.** Continues
  `187-tasks.symbols-ranges-via-coverage-caggs-api-load-test-tier-1.md` (Tasks
  1–9) — read that file's Context Summary for the slice's full framing before
  starting Task 10. Do not begin this file until file 1's tasks are complete
  and its tests pass.
- **Design reference:** `user/slices/187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier.md`.
  Decisions **D9–D12** are the primary reference for this file's tasks; read
  them in full before starting Task 11.
- **Load tier is a new tier for `api_server`.** Follow slice 167's established
  conventions exactly — `pytest.mark.skipif` on `MT_RUN_LOAD_TESTS`, the
  ephemeral-DB fixture, and `test_load_tier_never_references_prod_db_url`
  (`test/load/test_167_data_status_nfr.py`) — rather than inventing new ones.
- **Every load-tier bound is provisional** until the Task 15 walkthrough
  re-derives it from a measurement. A bound invented rather than measured is
  the failure mode D10 explicitly warns against.
- **Branch:** `187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier`
  (same branch as file 1 — do not create a second branch).

---

## Task 10 — Integration test: real statements against real caggs

- [ ] Add `test/integration/test_symbol_ranges_sql.py` (D2)
  - [ ] Against an ephemeral DB seeded with `minute_coverage`/`daily_coverage`
        and raw `minute_ohlcv`/`daily_ohlcv` rows covering the four D2 cases
        (spanning, before-edge-only, after-edge-only, no-data) for at least
        one symbol each
  - [ ] Execute the three real SQL statements from `queries.py` (not mocks) and
        assert the merged `(start, end)` per family matches a hand-computed
        expectation for every case
  - [ ] Follow `test_data_status_equivalence.py`'s precedent for
        date-normalized comparison — bucket truncation on the minute side is
        expected and does not indicate a defect (D2's byte-identical-at-date-grain
        claim)
  - [ ] Success: tests pass against `MT_TIMESCALE_TEST_URL`; no test depends on
        production data
  - [ ] Effort: 3

---

## Task 11 — Load-tier fixtures: extract and add dense-minute

- [ ] Create `test/load/conftest.py` and extract `prod_shaped_db` (D10)
  - [ ] Move `_symbol`, `_seed_prod_shape`, `_refresh_coverage`, and the
        `prod_shaped_db` fixture out of `test_167_data_status_nfr.py` into
        `conftest.py`, unchanged in behavior; update the 167 test's imports
        (pytest fixtures in `conftest.py` are auto-discovered, so no explicit
        import needed there, but confirm the 167 test still passes)
  - [ ] Add the dense-minute fixture: one symbol, a `1m` window of ~120 days x
        960 extended-hours bars (~115k rows) via `COPY`, exceeding
        `API_MAX_BARS_PER_REQUEST` (75,000) — this is what assertion 2 (Task 12b)
        needs
  - [ ] Confirm `test_load_tier_never_references_prod_db_url`'s scan (it globs
        every `*.py` in `test/load/`) still passes with the new `conftest.py`
        present — it must not read `MT_TIMESCALE_DB_URL`
  - [ ] Success: `uv run pytest test/load/test_167_data_status_nfr.py` (gated,
        `MT_RUN_LOAD_TESTS=1`) still passes after the extraction, proving no
        behavior moved
  - [ ] Effort: 2

---

## Task 12a — Load tier: symbol-detail and status latency (D10.1, D10.4)

- [ ] Create `test/load/test_187_api_nfr.py` and add assertions 1 and 4 — both are single-request latency bounds on the same app instance, so they share setup
  - [ ] `pytestmark` matches slice 167's convention: `skipif` on
        `MT_RUN_LOAD_TESTS`, plus any needed `pytest.mark.timeout`; module
        docstring opens the file (further gating text added in Task 12d)
  - [ ] Assertion 1 (D10.1): symbol-detail latency via `httpx.ASGITransport`
        against `create_app(db_url=prod_shaped_db)`; reset the
        freshness/universe-edge caches before each measured run. Provisional
        bound `< 250 ms`, median over repeated calls; docstring states the
        bound must be re-derived from the Phase 6 walkthrough measurement, not
        treated as final
  - [ ] Assertion 4 (D10.4): `GET /api/v1/status` against `prod_shaped_db`;
        provisional bound `< 1.5 s`, reusing slice 167's sub-second DB-side
        NFR plus serialization headroom
  - [ ] Success: both assertions pass against `prod_shaped_db`
  - [ ] Effort: 3

---

## Task 12b — Load tier: bars request latency at the admission ceiling (D10.2, the headline assertion)

- [ ] Add assertion 2 to `test_187_api_nfr.py`
  - [ ] Uses the dense-minute fixture (Task 11); a `1m` request at exactly
        `MT_API_MAX_BARS_PER_REQUEST` bars
  - [ ] Provisional bound `< 15 s`; assert the measured wall clock exceeds the
        configured `statement_timeout` **without** a `504` — this is the gap
        `statement_timeout` structurally cannot close (186 D12b), and is the
        point of the test
  - [ ] Success: passes against the dense-minute fixture; module docstring
        records that `httpx.ASGITransport` does not exercise uvicorn's HTTP
        layer (documented limitation, not a defect)
  - [ ] Effort: 3

---

## Task 12c — Load tier: concurrency / pool contention (D10.3, feeds D11)

- [ ] Add assertion 3 to `test_187_api_nfr.py`
  - [ ] 16 concurrent symbol-detail requests against the app's real pool
        (`max_size=8`); all complete; none exceeds assertion 1's single-request
        bound by more than a stated queueing factor
  - [ ] Record the measured per-request latencies at concurrency 16 — this
        output is Task 13's input, not just a pass/fail
  - [ ] Success: passes against `prod_shaped_db`; latencies captured for Task 13
  - [ ] Effort: 3

---

## Task 12d — Load tier: gating and module docstring (D9)

- [ ] Finish `test_187_api_nfr.py`'s gating and documentation, and run the full tier
  - [ ] Module docstring documents the manual gate command
        (`MT_RUN_LOAD_TESTS=1 uv run pytest test/load/`) and states CI wiring
        is slice 907's deliverable, not this slice's (D9)
  - [ ] Success: `MT_RUN_LOAD_TESTS=1 uv run pytest test/load/` runs all four
        assertions (Tasks 12a–12c) plus the existing 167/146 tests and
        `test_load_tier_never_references_prod_db_url` green
  - [ ] Effort: 1

---

## Task 13 — D11 pool-sizing decision

- [ ] Record the pool/consolidation decision from Task 12c's assertion-3 measurement
  - [ ] Using assertion 3's captured latencies, decide whether the three-pool
        arrangement (`app.state.db_pool` + the two class-owned DB pools) costs
        something real at concurrency 16 against `max_size=8`
  - [ ] Write the decision into design D11 (or a short addendum section) with
        the actual numbers — "not now" is an acceptable outcome if the
        numbers don't demand consolidation, but it must be written down with
        evidence, not left implicit
  - [ ] If consolidation is warranted, implement it in this slice: success
        criterion 8 requires the decision be recorded either way, but if the
        numbers demand a change, D11 says it lands here
  - [ ] Success: design document's D11 section (or an appended note) states
        the decision and cites the measurement; if code changed, tests from
        file 1's Tasks 5–9 still pass
  - [ ] Effort: 2

---

## Task 14 — Documents corrected (D12)

- [ ] `180-arch.data-serving.md` — Symbol Detail section
  - [ ] Replace the paragraph attributing the ranges cost to
        `minute_5min_ohlcv` and calling both queries "sub-millisecond index
        seeks" with the D1 measurement and the D2 read path
  - [ ] Success: section matches what was actually built in file 1's Tasks 6–9
  - [ ] Effort: 1

- [ ] `180-slices.data-serving-api.md`, entry 7 — verification only, no implementation
  - [ ] The `(187)` index and the corrected scope text (no guard-based
        fallback, CI gating noted as belonging to 907) were already
        materialized in Phase 4 (commit `567f0a0`). Confirm the entry still
        matches what Tasks 1–13 actually built; edit only if a design
        decision changed during implementation (e.g. the D11 outcome)
  - [ ] Success: entry text matches the shipped code; no unexplained diff
  - [ ] Effort: 1

- [ ] `README.md` — `available` semantics
  - [ ] Document: leading edge is exact (head probe), start is as of the last
        coverage materialization (D4), and the D3 residual window (quantified
        in Task 15's walkthrough step 4)
  - [ ] Success: a client dev can learn the actual contract, including its one
        documented gap, from the README alone
  - [ ] Effort: 1

- [ ] `CHANGELOG.md`
  - [ ] Entry under `[Unreleased]`: the `available` computation change and its
        latency improvement, the coverage-freshness content-edge check and its
        effect on `/api/v1/health` and `/api/v1/status`, and the new load tier
  - [ ] Note the coverage-staleness flip is expected and tracked as slice 169
  - [ ] Success: entry names all three user-visible effects
  - [ ] Effort: 1

- [ ] Regenerate `docs/api/openapi.json`
  - [ ] `uv run python scripts/dump_openapi.py`; confirm the drift test
        (`test_openapi_artifact.py`, slice 186) shows no diff for the symbols
        route — response shape is unchanged (D2)
  - [ ] Success: drift test passes; diff (if any) touches only `info.version`
  - [ ] Effort: 1

---

## Task 15 — Prod verification walkthrough

- [ ] Run the design's Verification Walkthrough steps 1–3, 5, 9 against prod `trading`
  - [ ] Step 1: before/after latency on `GET /api/v1/symbols/{SPY,AAPL,F,<delisted>}`,
        five calls each, medians reported against the D1 baseline
  - [ ] Step 2: `EXPLAIN (ANALYZE, BUFFERS)` each of the three new statements
        with production parameters; confirm single-digit-ms planning and chunk
        exclusion (success criterion 1 evidence)
  - [ ] Step 3: merged vs. lazy `MIN/MAX` equivalence across 20+ symbols
        (dense, sparse, delisted, minute-only, daily-only, no-data) — success
        criterion 2
  - [ ] Step 5: `check_coverage_freshness` against prod reports stale with
        `CONTENT_EDGE_TOO_OLD` and the observed lag; `mt data status`,
        `/api/v1/health`, `/api/v1/status` all reflect it; `/api/v1/symbols/SPY`
        still returns the correct current daily edge — proving D2's
        independence from the verdict
  - [ ] Step 9: `/api/v1/symbols/SPY` response body diffed against a pre-slice
        capture — identical apart from the D2-corrected values; OpenAPI drift
        test green
  - [ ] Success: each step's actual output recorded for the walkthrough rewrite
        below; every `statement_timeout` set explicitly per project convention
  - [ ] Effort: 3

- [ ] Run step 4: quantify the D3 residual window (load-bearing — success criterion 2's caveat clause)
  - [ ] Across the same 20+ symbol sample, compute each symbol's own coverage
        end against the universe edge and check whether any raw bar falls in
        the gap
  - [ ] Report the count and largest discrepancy
  - [ ] **If the gap is not negligible**, apply the documented fallback before
        closing the slice: per-symbol bounding for symbols whose coverage end
        is recent, universe edge otherwise (D3's stated fallback) — this is a
        conditional task; do not implement it speculatively if step 4 shows the
        gap is negligible
  - [ ] Success: finding recorded in the walkthrough; fallback applied only if
        warranted, with its own test coverage added under file 1 Task 7's file
        if so
  - [ ] Effort: 2

- [ ] Run steps 6–8: load tier against prod-shaped measurements
  - [ ] Step 6: the detection-floor test (file 1 Task 3) shown failing when
        `_raw_max`'s alignment is removed, passing when restored
  - [ ] Step 7: `MT_RUN_LOAD_TESTS=1 uv run pytest test/load/` end to end;
        record each assertion's measured median and the bound derived from it
  - [ ] Step 8: assertion 3's per-request latencies at concurrency 16 reported
        alongside the Task 13 D11 decision
  - [ ] Success: all measurements recorded for the walkthrough rewrite
  - [ ] Effort: 2

- [ ] Rewrite the design's Verification Walkthrough section
  - [ ] Replace the draft walkthrough with the actual commands run and
        observed output from this task's three prior items; bounded to that
        one section plus the two explicitly-flagged provisional-bound
        corrections in D10 if measurements warrant adjusting them
  - [ ] Do not revise decisions D1–D12 here beyond the bound corrections D10
        explicitly calls for
  - [ ] Success: walkthrough section reflects reality; no other section edited
  - [ ] Effort: 2

---

## Task 16 — Close-out

- [ ] Full verification
  - [ ] `uv run pytest test/unit test/integration -q` — compare pass count and
        error list against file 1 Task 1's baseline
  - [ ] `MT_RUN_LOAD_TESTS=1 uv run pytest test/load/` green
  - [ ] `uv run --extra dev mypy src/manta_trading/api_server/ src/manta_trading/data/maintenance/status_coverage.py src/manta_trading/market/maintenance/cagg_freshness.py` clean;
        `ruff` clean on every touched file
  - [ ] `docs/api/openapi.json` regenerated as the final step so it reflects
        the merged state
  - [ ] `cf check` clean
  - [ ] Success: suite green, static analysis clean on touched files, `cf check`
        clean
  - [ ] Effort: 1

- [ ] Commit and merge
  - [ ] Semantic commits throughout; mark tasks complete via `task-checker`
  - [ ] Merge to `main` with `--no-ff` and the message
        `Merge slice 187: symbols ranges via coverage caggs + API load-test tier`
  - [ ] Success: `main` green, `cf check` clean post-merge, branch left in place
  - [ ] Effort: 1
