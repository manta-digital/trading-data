---
docType: tasks
slice: api-reference-documentation-one-for-humans-one-for-agents
project: trading-data
lld: user/slices/190-slice.api-reference-documentation-one-for-humans-one-for-agents.md
dependencies: [186, 188, 189]
projectState: >
  17 API paths serve equity bars, the Kalshi catalog and tape, and operations
  state. docs/api/openapi.json is committed and drift-gated (186 D7). D3 of this
  slice — publishing the three suppressed 200 schemas — already landed on main in
  commit 3141815, verified byte-identical on all six live responses; its tasks
  here are verification and artifact assertions, not new implementation. No
  consumer-facing reference exists; README 554-774 is an endpoint list with
  essays attached.
dateCreated: 20260916
dateUpdated: 20260916
status: not_started
---

## Context Summary

- Working on the **190 API reference documentation** slice: two hand-written
  consumer documents plus a mechanical gate that keeps them from drifting.
- **Delivers**: `docs/api/reference.md` (route-indexed, for humans),
  `docs/api/agents.md` (capability-indexed, for LLM clients),
  `scripts/check_api_docs.py` (coverage gate), `test/unit/api_server/test_api_docs.py`,
  and a README that points at the reference instead of competing with it.
- **Already done before task 1**: D3, the slice's only code change, landed in
  commit `3141815`. `BarsResponse`, `CandlesResponse` and `TradesResponse` now
  publish a `200` listing both media types. SC7's byte comparison was executed
  and passed (6/6 responses identical). Tasks 1.1–1.3 assert that in the suite
  rather than redoing it.
- **Prerequisites** (all landed): 189 (`/overview`, `/credits` complete the
  surface), 186 D7 (`scripts/dump_openapi.py`, `ARTIFACT_PATH`, the drift test
  whose loader pattern the gate's test reuses), 188 (`api_server/admission.py`
  wire-message constants the error docs quote).
- **Order rationale**: the gate is built first (section 2) because it defines
  the `<!-- from: -->` markers the prose must carry — writing seventeen sections
  first means retrofitting all of them (design, Implementation Notes).
- **Next planned slice**: production deploy script (PM-directed, after 190).

### Symbols this slice reads rather than retypes

Three of these names exist in more than one module. The gate's markers must use
the module the **API itself imports**, verified 2026-09-16:

| Marker symbol | Why this module |
|---|---|
| `manta_trading.constants.API_MAX_BARS_PER_REQUEST` | `constants.py:1287`, the 75,000 ceiling |
| `manta_trading.constants.Granularity` | imported by `routes/gaps.py:18`, `routes/symbols.py:26` — **not** `data.acquisition.state.Granularity` |
| `manta_trading.cli.rendering.status_table.HealthStatus` | imported by `routes/status.py:25` |
| `manta_trading.data.kalshi.constants.MarketStatus` | imported by `routes/kalshi_catalog.py:42` — **not** `data.base.trading_calendar.MarketStatus` |
| `manta_trading.data.acquisition.pass_runs.PassKind` | imported by `models/operations.py:21` |
| `manta_trading.data.acquisition.pass_runs.PassRunOutcome` | imported by `models/operations.py:21` |

---

## 1. Confirm the landed code change (D3)

### [ ] Task 1.1 — Assert the three response models are published in the artifact
- **Effort**: 1
- [ ] Extend `test/unit/api_server/test_openapi_artifact.py` (do not create a new
      file — this is the artifact's test) with assertions read from the
      **committed** `docs/api/openapi.json`, not the live app, matching the
      existing file's stated reasoning.
- [ ] Assert `components/schemas` contains all eight types: `BarsResponse`,
      `BarRecord`, `CandlesResponse`, `CandleRecord`, `CandleBidAsk`,
      `CandlePrice`, `TradesResponse`, `TradeRecord`.
- [ ] Assert each of the three paths (`/api/v1/bars/{symbol}`,
      `/api/v1/kalshi/markets/{ticker}/candlesticks`,
      `/api/v1/kalshi/markets/{ticker}/trades`) publishes a `200` whose
      `content` names **both** `application/json` and `application/x-msgpack`.
- [ ] **Success**: `uv run pytest test/unit/api_server/test_openapi_artifact.py -q`
      passes; each assertion fails if its type or media type is removed (verify
      by temporarily editing the artifact, then reverting).

### [ ] Task 1.2 — Assert the seven field descriptions reach the artifact
- **Effort**: 1
- [ ] In the same test file, assert the `Field(description=…)` strings on
      `CandlesResponse` and `TradesResponse` record types appear in the
      committed artifact's schema components. Read the expected strings from the
      model classes, not retyped literals — a test that hard-codes the prose
      passes when the model's description is deleted.
- [ ] **Success**: test passes; deleting one `description=` from a model and
      regenerating the artifact fails this test.

### [ ] Task 1.3 — Record the SC7 evidence in the slice's verification trail
- **Effort**: 1
- [ ] SC7's byte comparison was executed against commit `3141815` (6/6 responses
      identical: 3 routes × json/msgpack). Confirm the schema diff is still
      confined to those three paths: regenerate with
      `uv run python scripts/dump_openapi.py --check` and confirm no drift.
- [ ] Confirm the artifact still declares exactly 17 paths.
- [ ] **Success**: `--check` exits 0; `jq '.paths | keys | length'` returns 17.
      No new work if both hold — this task is a gate on the assumption the rest
      of the slice rests on.
- [ ] **Commit** after 1.1–1.3: `test: assert the three published 200 schemas in the artifact`

---

## 2. The coverage gate (build before the prose)

### [ ] Task 2.1 — Gate skeleton: load the artifact, parse paths from both documents
- **Effort**: 3
- [ ] Create `scripts/check_api_docs.py` mirroring `dump_openapi.py`'s shape:
      module docstring, `--check` flag, exit 1 with a remedy line, **no database
      required**.
- [ ] Read the committed `docs/api/openapi.json` (not the live app), reusing
      `dump_openapi.ARTIFACT_PATH` rather than recomputing the path.
- [ ] Extract the set of documented paths from each of `docs/api/reference.md`
      and `docs/api/agents.md`.
- [ ] **Decide and record how a path is declared in the prose.** The design
      specifies parsing Markdown for path mentions; a regex over English is the
      fragile-parsing trap this project's rules warn against. Prefer an explicit
      structured marker (a fenced block, an HTML comment, or a required heading
      form) over inferring from arbitrary prose. Document the chosen convention
      at the top of the gate and in both documents' "Keeping this accurate"
      section, so a future author knows what makes a path *documented*.
- [ ] Both documents do not exist yet at this point: the gate must fail cleanly
      with a named missing-file error, not a traceback.
- [ ] **Success**: `uv run python scripts/check_api_docs.py --check` exits 1
      naming both missing documents; `--help` renders; no DB env var needed.

### [ ] Task 2.2 — Path coverage, both directions
- **Effort**: 2
- [ ] Every path in the artifact appears in **both** documents; report each
      missing (path, document) pair by name.
- [ ] Every path named in either document exists in the artifact; report each
      unknown path by name and document.
- [ ] **Success**: with no documents present the failure lists all 17 paths
      twice; the error text names the offending path and file, never a count
      alone.

### [ ] Task 2.3 — Query parameter coverage and types
- **Effort**: 3
- [ ] For each path, every query parameter documented must exist on that path in
      the artifact, with the documented type.
- [ ] For each path, every parameter declared in the artifact must be documented.
- [ ] Report failures as (path, parameter, documented value, declared value).
- [ ] **Success**: renaming a parameter in the artifact produces a failure naming
      the path and both spellings.

### [ ] Task 2.4 — Error-status subset check
- **Effort**: 2
- [ ] Documented error statuses for a path must be a **subset** of the statuses
      declared for that path in the artifact.
- [ ] `/api/v1/credits` declares only `200` (189 D8, it issues no statement), so
      documenting a `504` there must fail. Treat this as the check's defining
      case, not an edge case.
- [ ] **Success**: adding `504` to the credits section of a document fails the
      gate with the path and status named.

### [ ] Task 2.5 — `from:` marker resolution
- **Effort**: 3
- [ ] Parse `<!-- from: dotted.symbol.path -->` markers, import the named symbol,
      and compare it to the documented value.
- [ ] Scalars (the 75,000 ceiling) compare by value; enums compare as **token
      sets**, so a new member fails rather than quietly extending a documented
      list.
- [ ] Use the six fully-qualified symbols in the Context Summary table. A marker
      naming an unimportable symbol, or a symbol that is not one of these, fails
      with the dotted path in the message — silent skipping of an unresolvable
      marker defeats the check.
- [ ] **Success**: a marker naming a nonexistent symbol fails; a marker whose
      documented enum set omits one member fails, naming the symbol and the
      difference.

### [ ] Task 2.6 — Gate report output and remedy lines
- **Effort**: 2
- [ ] Without `--check`: print a report of what was checked (paths, parameters,
      statuses, markers) and exit 0 regardless.
- [ ] With `--check`: exit 1 on any failure; each failure line names the file,
      the path/symbol, and what to do about it — following `dump_openapi.py`'s
      remedy-line convention.
- [ ] A passing `--check` prints a line naming the number of paths checked (17).
- [ ] **Success**: both invocations behave as specified on the current tree.
- [ ] **Commit** after 2.1–2.6: `feat: add check_api_docs.py coverage gate`

### [ ] Task 2.7 — Gate tests, including proof it fails
- **Effort**: 3
- [ ] Create `test/unit/api_server/test_api_docs.py`, importing the gate by path
      using the loader pattern in `test_openapi_artifact.py:23-38` (`scripts/` is
      not an installed package; import the real module, not a copy of it).
- [ ] Assert the gate passes on the current tree — this will fail until section
      3 and 4 land, so mark this assertion as the one task 5.1 re-runs.
- [ ] **Assert the gate detects each drift class from a mutated fixture**: an
      added path, a renamed parameter, a changed ceiling value. A gate not proven
      to fail is not a gate. Mutate a copied fixture, never the committed
      artifact, and assert the specific failure message.
- [ ] Assert the `/api/v1/credits` 504 case fails (task 2.4's defining case).
- [ ] Assert an unresolvable `from:` marker fails rather than being skipped.
- [ ] **Success**: `uv run pytest test/unit/api_server/test_api_docs.py -q` — the
      mutation tests pass now; the whole-tree pass assertion goes green at 5.1.
- [ ] **Commit**: `test: add drift-detection tests for the API docs gate`

---

## 3. `docs/api/reference.md` — the human reference

Route-indexed, one section per path. Structure is specified in the design,
"Deliverable Specification". Field tables give name, type, nullability, **unit or
time base**, and meaning — the unit column is the reason the document exists.

### [ ] Task 3.1 — Front matter, scope statement, quick start
- **Effort**: 2
- [ ] Sections 1–2: what this is and is not; base URL; `mt serve`; `/docs`; a
      first `curl`.
- [ ] State that example **values are illustrative and shapes are contractual**
      (D6), once, at the top.
- [ ] Disambiguate `docs/api/foundation_usage_examples.md`: despite its path it
      documents slice-750 Python library modules, not the HTTP API.
- [ ] **Success**: a reader who opens the wrong neighbouring file is redirected
      by this section alone.

### [ ] Task 3.2 — Conventions: time, windows, numbers
- **Effort**: 3
- [ ] 3.1 Time: every `*_ts`/`_time`/`_at` is UTC; naive input is read as UTC.
- [ ] 3.2 Date windows: inclusive at both ends for bars and Kalshi; state the
      `/api/v1/gaps` exception (next-midnight-exclusive day end) **and why** —
      the 2004-01-01 SPY defect recorded at `routes/windows.py:28-34`.
- [ ] 3.3 Numbers: `_dollars`/`_fp` fields are decimal **strings** on the wire,
      and why (`serialization.py:30-36`, `mode="json"` stringifies `Decimal`).
- [ ] **Success**: each of the three subsections states a rule and its exception;
      the gaps exception cites the reason, not just the behavior.

### [ ] Task 3.3 — Conventions: empty results, the range cap, staleness
- **Effort**: 3
- [ ] 3.4 Empty results: the four meanings of an empty Kalshi result; 200-not-404.
      Move this essay from README 619-635 in full — moved, not rewritten.
- [ ] 3.5 The range cap: the ceiling carrying
      `<!-- from: manta_trading.constants.API_MAX_BARS_PER_REQUEST -->`; estimate
      (bars) vs exact count (Kalshi); the span table; rows-not-bytes.
- [ ] 3.6 Staleness: which endpoints carry which signal and what to do about
      each. Include that raw grains (`1m`, `1d`) are never stale **by
      construction** — no cagg to be behind — not because they happen to be fresh.
- [ ] **Success**: the ceiling appears with its marker and the gate resolves it;
      no numeric ceiling is retyped anywhere in the document.

### [ ] Task 3.4 — Conventions: errors, including both 422 shapes
- **Effort**: 3
- [ ] 3.7 Errors: the `404`/`422`/`500`/`504` shapes. Move the error-shape table
      from README 701-723.
- [ ] Document the **422 shape mismatch** (D7): OpenAPI declares
      `HTTPValidationError` (`{"detail": [{"loc","msg","type"}]}`) for 422 on
      every route, but all eight hand-written 422s send `{"error": "…"}` through
      the handler at `app.py:192`. State both shapes and **which conditions
      produce which** — FastAPI's own request-validation failures produce the
      declared shape, hand-written refusals produce the other.
- [ ] Quote the remedy strings from `api_server/admission.py:21,24`
      (`NARROW_FILTER`, `NARROW_WINDOW`) verbatim.
- [ ] 3.8 Auth, CORS, pagination: the posture, stated.
- [ ] **Success**: SC10 — both shapes documented with their producing conditions.

### [ ] Task 3.5 — Endpoint sections: equity bars, symbols, status, gaps, health (6 paths)
- **Effort**: 4
- [ ] One section each for `/api/v1/health`, `/api/v1/bars/{symbol}`,
      `/api/v1/symbols`, `/api/v1/symbols/{symbol}`, `/api/v1/status`,
      `/api/v1/gaps/{symbol}`, and the `/api/v1/status` vocabulary note.
- [ ] Each section: purpose · parameters · response fields with type,
      nullability and unit/time base · errors · an executed `curl` with real
      output and its capture date.
- [ ] Mine the docstrings rather than working from memory: `StatusRowRecord`
      (`models/responses.py:211-221` — `gap_count` counts open gaps only;
      `health STALE` changed meaning in slice 922), `CoverageVerdict`
      (`models/responses.py:171-178` — `lag_seconds` is a float and `None` is not
      `0.0`), `window_end_utc` (`routes/windows.py:28-34`).
- [ ] Document the three related-looking granularity parameters **once, with a
      cross-reference to Conventions**: `/api/v1/status` takes `daily|minute`;
      `/api/v1/gaps` takes `Granularity` and maps it to the family at
      `gaps.py:29`; bars takes `Granularity` directly.
- [ ] Wherever this document lists the `Granularity` or `HealthStatus` token
      sets, carry their markers —
      `<!-- from: manta_trading.constants.Granularity -->` and
      `<!-- from: manta_trading.cli.rendering.status_table.HealthStatus -->` —
      exactly as 3.6 does for `MarketStatus` and 3.7 for `PassKind`. A
      vocabulary listed in `reference.md` without a marker is drift the gate
      cannot see in this file, even though `agents.md` carries the same set
      (D2: the two documents describe one surface and must not fork). Note
      `/api/v1/status`'s `daily|minute` is a **different** vocabulary and is not
      `Granularity` — do not mark it as such.
- [ ] State that `count: 0` on `/api/v1/status` means *nothing wrong*, not *no
      such symbol* — the API's most expensive misreading.
- [ ] **Success**: SC1 for these 6 paths; every parameter in the artifact has a
      row; the gate's parameter check passes for them. (6 + 7 + 4 = 17.)

### [ ] Task 3.6 — Endpoint sections: Kalshi catalog (7 paths)
- **Effort**: 4
- [ ] One section each for `/api/v1/kalshi/categories`, `/series`,
      `/series/{ticker}`, `/series/{ticker}/events`, `/events/{event_ticker}`,
      `/events/{event_ticker}/markets`, `/markets/{ticker}`.
- [ ] `MarketStatus` vocabulary carries
      `<!-- from: manta_trading.data.kalshi.constants.MarketStatus -->`.
- [ ] State the delegation: `MarketRecord` uses Kalshi's field names verbatim and
      delegates their semantics to Kalshi (`models/kalshi_catalog.py:201-206`).
      Link out rather than paraphrasing a vocabulary this project does not own.
- [ ] Include `MarketSettlement`'s docstring facts (`models/kalshi_catalog.py:149-153`).
- [ ] Use a **settled** Kalshi market in examples so a reader re-running one gets
      output rather than a 404 (D6).
- [ ] **Success**: SC1 for these 7 paths; the `MarketStatus` marker resolves.

### [ ] Task 3.7 — Endpoint sections: Kalshi time series and operations (4 paths)
- **Effort**: 3
- [ ] `/api/v1/kalshi/markets/{ticker}/candlesticks`, `/trades`, plus
      `/api/v1/overview` and `/api/v1/credits`.
- [ ] Carry the seven `Field(description=…)` strings through to the field tables.
- [ ] `/api/v1/overview`: `PassKind` and `PassRunOutcome` vocabularies with their
      `from:` markers; `RunningRecord` docstring facts
      (`models/operations.py:38-48`); note that the `pass` field is serialized by
      **alias** (`models/operations.py:81,87`) because `pass` is a Python
      keyword — a Python consumer cannot use it as an attribute name.
- [ ] `/api/v1/credits`: always `200`; an unset key or unreachable provider
      arrives as `error` text with `credits: null`. **No `504`** — it issues no
      statement (189 D8).
- [ ] **Success**: SC1 for all 17 paths once 3.5–3.7 are complete.

### [ ] Task 3.8 — Field glossary and "Keeping this accurate"
- **Effort**: 2
- [ ] Section 5: glossary for `is_stale`, `coverage`, `gap_count`, `lag_seconds`,
      `pass`, `count`, `available`, `tape_filtered`. Move the `available`
      semantics essay from README 670-700.
- [ ] Section 6: how to run the gate, the marker convention chosen in 2.1, and —
      stated plainly — **what the gate cannot check**: whether a sentence of
      prose is true. Prose accuracy is the reviewer's and the walkthrough's job.
- [ ] **Success**: a contributor can add a route and know what to write and what
      to run, from this section alone.

### [ ] Task 3.9 — msgpack savings: measured figures, not the inherited estimate
- **Effort**: 1
- [ ] Where the reference describes what `format=msgpack` saves, publish the
      **measured per-route table** from the design's D3 with its capture date
      (2026-09-16): bars 35.5–36.0%, Kalshi candlesticks 20.1%, trades 14.2%.
- [ ] Do **not** restate the architecture's ~40–60%; it holds for none of the
      three routes that offer the parameter.
- [ ] Note briefly why Kalshi is worse: `mode="json"` stringifies `Decimal`
      prices before either encoder runs, and msgpack cannot pack a string
      tighter than JSON.
- [ ] **Success**: SC12 — every msgpack figure in the document is measured and
      dated; `grep` for "40" near msgpack finds no inherited estimate.
- [ ] **Commit** after 3.1–3.9: `docs: add docs/api/reference.md — the human API reference`

---

## 4. `docs/api/agents.md` — the agent reference

Capability-indexed. Structure is specified in the design, "Deliverable
Specification".

### [ ] Task 4.1 — Orientation and capability index
- **Effort**: 4
- [ ] Section 1: read-only; no auth; base URL; every response is JSON unless
      msgpack is asked for.
- [ ] Section 2: index by **question**, not by route. Each capability names its
      route, its response shape, and its `Not this:` near-misses (D2).
- [ ] Cover at minimum the three near-miss traps the design names:
      `/api/v1/status` for "is it running" (wrong — that is `/api/v1/overview`),
      `/api/v1/health` for "is my data fresh", `/api/v1/gaps` for "does this
      symbol exist". Each returns `200` with a confidently wrong answer.
- [ ] Every one of the 17 paths must be reachable from a capability entry — the
      gate checks this.
- [ ] **Success**: SC2's capability half; the gate's path-coverage check passes
      for this document.

### [ ] Task 4.2 — Choosing a window; reading a result
- **Effort**: 2
- [ ] Section 3: the cap (with its `from:` marker), the span table, and how to
      split a too-large request.
- [ ] Section 4: 200-with-zero-rows is the **normal** case; the four meanings of
      an empty result; how to tell "no data" from "wrong identifier".
- [ ] **Success**: an agent can derive a valid second request from a `422`
      response using only this section.

### [ ] Task 4.3 — Failure modes with retry verdicts
- **Effort**: 3
- [ ] Section 5 — the section that earns the separate file. **Every status gets
      an explicit retry verdict and the parameter change that resolves it**:
      `422` retry will not help, narrow the window; `504` retry might help, the
      query was cancelled; `200` with `count: 0` nothing is wrong.
- [ ] State what a `504` does **not** mean (review F002): `statement_timeout`
      bounds a single statement, not a request, so a route issuing several
      statements can run far past the budget without any one being cancelled —
      186 D12b measured a 95-second request under a 20-second budget
      (`180-arch.data-serving.md:291`). Request latency is not enforced anywhere.
      Two consequences stated explicitly, so an agent need not infer them: a slow
      request is not a hung one and may still return; the absence of a `504` is
      no evidence a request was fast.
- [ ] Document both `422` shapes here too (D7) — an agent parsing the declared
      shape will mis-parse eight of the API's most informative messages.
- [ ] **Success**: SC2's failure-mode half; every status appearing anywhere in
      the artifact has a verdict here.

### [ ] Task 4.4 — Determinism, freshness, vocabularies
- **Effort**: 2
- [ ] Section 6: what is safe to poll — `/api/v1/overview` is a pure DB read;
      `/api/v1/credits` makes an outbound call — and what can be stale and how
      you are told.
- [ ] Section 7: the enum token sets, each with its `<!-- from: -->` marker using
      the fully-qualified paths in the Context Summary table. Five vocabularies,
      none retyped.
- [ ] **Success**: SC5 — the gate resolves all six markers (ceiling + five enums)
      across both documents.
- [ ] **Commit** after 4.1–4.4: `docs: add docs/api/agents.md — the agent API reference`

---

## 5. Close the loop: gate green, README, examples verified

### [ ] Task 5.1 — Run the gate against the completed documents
- **Effort**: 2
- [ ] `uv run python scripts/check_api_docs.py --check` — must exit 0 and print
      the 17-paths-checked line.
- [ ] The whole-tree pass assertion deferred in task 2.7 now goes green.
- [ ] Fix any coverage failure in the **documents**, not by weakening the gate.
      A check relaxed to make prose pass is the failure this slice exists to
      prevent.
- [ ] **Success**: SC3 (gate passes and still fails on all three mutations) and
      SC4 (`uv run pytest test/unit/api_server/test_api_docs.py -q` green).
- [ ] **Commit**: `test: gate the API documentation in the unit tier`

### [ ] Task 5.2 — Integration tests confirm example shapes
- **Effort**: 3
- [ ] Add integration tests under `test/integration/api_server/` asserting every
      documented `curl` example returns the documented **shape**: status code,
      top-level keys, field types. **Not values** (D6).
- [ ] Each example block in both documents carries its capture date.
- [ ] **Success**: SC8 — `uv run pytest test/integration/api_server -q` green
      (allowing the documented pre-existing baseline failures); changing a
      documented top-level key fails a test.
- [ ] **Commit**: `test: assert documented API example shapes`

### [ ] Task 5.3 — README: the endpoint list becomes a pointer
- **Effort**: 3
- [ ] Verify the essays are present in `reference.md` **before** deleting them
      from the README — creation and verification precede deletion so the prose
      stays recoverable.
- [ ] Delete the endpoint list (README 570-618) and the moved essays
      (619-635 empty-result, 670-700 `available`, 701-723 error shapes, 724-737
      inclusive windows and 200-not-404, 738-758 range cap).
- [ ] **Keep**: `mt serve` invocation (557-568), the `MT_API_*` environment table
      and its interaction note (759-769), the one-paragraph statement of what the
      API serves, and the auth/CORS paragraph (770-773) — a security-relevant
      fact a reader must not have to follow a link to find.
- [ ] Add a link to `docs/api/reference.md`.
- [ ] Rewrite anchor links into the moved sections; confirm no dead anchors
      remain anywhere in the README.
- [ ] **Success**: SC9 — `grep -n "GET /api/v1" README.md` returns no endpoint
      list; `mt serve`, the `MT_API_*` table and the auth/CORS paragraph are
      still present; no dead anchors.
- [ ] **Commit**: `docs: point the README at the API reference`

### [ ] Task 5.4 — Record the 422 fix as Future Work
- **Effort**: 1
- [ ] Add a Future Work entry to `user/architecture/180-slices.data-serving-api.md`
      for fixing the 422 shape mismatch — it changes a published schema, which is
      a client-visible contract change, out of place in a slice whose defining
      constraint is that nothing served changes.
- [ ] Mark plan entry 10 as materialized as **(190)**.
- [ ] **Success**: SC10's second half and SC11.
- [ ] **Commit**: `docs: record the 422 shape fix as future work` — its own
      commit, not folded into 5.6. This edits the architecture tree, which 5.6's
      message does not mention.

### [ ] Task 5.5 — Verification walkthrough
- **Effort**: 3
- [ ] Execute all eleven steps of the design's Verification Walkthrough against a
      running API. **Start a current server on a spare port**
      (`uv run mt serve --host 127.0.0.1 --port 8137` with `.env` exported) —
      port 8100 runs the old 0.15.1 production build with only 6 endpoints and
      cannot verify current-checkout routes.
- [ ] Step 2 and step 3 require deliberately breaking the tree (add a parameter;
      change the ceiling) and confirming `exit=1`, then reverting. A gate never
      seen to fail has not been verified.
- [ ] Step 6 is the document's real test: answer three questions about
      `/api/v1/bars/{symbol}` **without opening any source file**.
- [ ] Record the outcome of each step; any step that does not produce its
      expected output is a defect to fix, not a note to file.
- [ ] **Success**: all eleven steps produce their documented expected output.
- [ ] **Commit** any defect fixed during the walkthrough before 5.6, with a
      message naming the step that caught it. If every step passed with no
      change, there is nothing to commit and that is the expected outcome.

### [ ] Task 5.6 — Full suite and final verification
- **Effort**: 2
- [ ] `MT_TIMESCALE_TEST_URL` must be **exported** or ~40 unit tests ERROR at
      fixture setup with a config message (not a code failure).
- [ ] `uv run pytest test/unit -q` — green.
- [ ] `uv run pytest test/integration -q` — green except the documented
      pre-existing baseline failures.
- [ ] `uv run mypy` and `uv run ruff check` clean. Scope `ruff format` to touched
      files only, and `git diff main` for unintended deletions before committing.
- [ ] Confirm SC1–SC12 each have a satisfied line in this task file.
- [ ] **Commit**: `docs: complete slice 190 API reference documentation`

---

## Success Criteria Coverage

| SC | Covered by |
|---|---|
| SC1 — reference documents all 17 paths | 3.5, 3.6, 3.7 |
| SC2 — agents.md capability-indexed with retry verdicts | 4.1, 4.3 |
| SC3 — gate passes and fails on three mutations | 2.7, 5.1 |
| SC4 — gate runs in the unit tier | 2.7, 5.1 |
| SC5 — every code-tracking value carries a `from:` marker | 2.5, 3.3, 3.6, 3.7, 4.4 |
| SC6 — three models in `components/schemas`, both media types | 1.1 (landed in `3141815`) |
| SC7 — byte-identical live responses | 1.3 (executed against `3141815`) |
| SC8 — example shapes tested, capture dates present | 5.2 |
| SC9 — README carries one description, not two | 5.3 |
| SC10 — both 422 shapes documented; fix recorded as Future Work | 3.4, 4.3, 5.4 |
| SC11 — plan entry 10 materialized as (190) | 5.4 |
| SC12 — measured msgpack figures, not the 40–60% estimate | 3.9 |

## Review Disposition (tasks review, 20260916, CONCERNS)

| Finding | Disposition |
|---|---|
| F001 — `Granularity`/`HealthStatus` markers missing from `reference.md` | **Fixed** in 3.5. Correct: SC5 was satisfiable via `agents.md` alone, which forks the two documents (D2). |
| F002 — 3.5 and 3.7 miscount their paths | **Fixed**. 3.5 is 6, 3.7 is 4; the errors cancelled in the total, so only the headers were wrong. |
| F003 — 5.4 and 5.5 have no commit checkpoint | **Fixed**. 5.4 now commits separately — it edits the architecture tree, which 5.6's message does not mention. 5.5 commits only if the walkthrough finds a defect. |
| F004 — D3 landed on `main`, not a slice branch | **Acknowledged, no change.** Accurate and not fixable from a task file. |
| F005 — stale "trap" comment in `test_openapi_operations_schema.py` | **Declined — premise is wrong.** See below. |

**F005 in detail.** The finding reads "both routes publish a 200 schema (SC9) —
the trap 188 recorded" as describing routes D3 has now fixed. It does not.
"Both routes" is `/api/v1/overview` and `/api/v1/credits` (the file's `OVERVIEW`
and `CREDITS` constants, lines 31-32) — slice 189's routes, which never
suppressed their schema. The sentence cites the 188 trap as the *reason those
two assert a 200 explicitly*, and that reasoning is still true. D3 fixed the
trap on three **different** routes: bars, candlesticks and trades. Nothing in
that comment is stale, and editing it would make it less accurate. The reviewer
marked this finding's location `unverified`, which is consistent with it having
been reasoned from the design's quotation rather than from the file.

## Notes

- **Open design question, worth resolving at task 2.1**: the design specifies the
  gate parses Markdown prose for path and parameter mentions. Neither slice
  review examined this. Parsing English with a regex is the exact fragile-parsing
  trap the project rules name; a structured marker in the documents is the
  lower-risk reading of the same requirement. Task 2.1 makes that call explicitly
  rather than discovering it during section 3.
- **The architecture's msgpack figure was wrong and is now corrected.**
  `180-arch.data-serving.md` gave ~40–60% in two places (the bars endpoint and
  the Serialization section); both now carry the measured per-route figures.
  Corrected in commit `7d30242`, so task 3.9 copies from the architecture rather
  than contradicting it.
- Phase 5 is planning work: it commits directly to `main`, no work branch.
  Implementation (Phase 6) takes branch `190-slice.api-reference-documentation-one-for-humans-one-for-agents`.
