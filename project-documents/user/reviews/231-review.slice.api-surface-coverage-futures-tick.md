---
docType: review
layer: project
reviewType: slice
slice: api-surface-coverage-futures-tick
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/231-slice.api-surface-coverage-futures-tick.md
aiModel: claude-opus-5-5
aiProfile: sdk
status: complete
dateCreated: 20261012
dateUpdated: 20261012
reviewedSha: ac963b64946b540db644bf5f3c54487b01b20ac0
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 9
durationSeconds: 67.2
squadronVersion: 0.21.5
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "The products and schedule routes have unclear outage behaviour, and their connections are unbounded"
    location: "project-documents/user/slices/231-slice.api-surface-coverage-futures-tick.md#catalog"
  - id: F002
    severity: concern
    category: documentation
    summary: "A third row-ceiling style is added without extending 180's Range Policy"
    location: "project-documents/user/slices/231-slice.api-surface-coverage-futures-tick.md#td4-one-row-ceiling-enforced-while-reading-no-pagination"
  - id: F003
    severity: pass
    category: scope
    summary: "Slice plan scope is fully covered and nothing is overbuilt"
    location: "project-documents/user/slices/231-slice.api-surface-coverage-futures-tick.md#technical-scope"
  - id: F004
    severity: pass
    category: architecture
    summary: "The serving-layer failure posture matches the architecture"
    location: "project-documents/user/slices/231-slice.api-surface-coverage-futures-tick.md#td2-the-tick-pool-opens-without-blocking-and-maps-failure-to-503"
  - id: F005
    severity: pass
    category: architecture
    summary: "The health-field vocabulary change is recorded"
    location: "project-documents/user/slices/231-slice.api-surface-coverage-futures-tick.md#td7-composed-lines-health-status-overview"
  - id: F006
    severity: pass
    category: architecture
    summary: "The active contract and roll rule are explicit on every response"
    location: "project-documents/user/slices/231-slice.api-surface-coverage-futures-tick.md#td5-response-shape-the-plans-facts-then-rows-exact-text-for-prices-and-times"
  - id: F007
    severity: note
    category: dependencies
    summary: "`TICK_SOURCE_NAME` is imported from a CLI module"
    location: "project-documents/user/slices/231-slice.api-surface-coverage-futures-tick.md:158-161"
  - id: F008
    severity: note
    category: performance
    summary: "The `/status` latency risk has no target"
    location: "project-documents/user/slices/231-slice.api-surface-coverage-futures-tick.md#technical-risks"
  - id: F009
    severity: note
    category: documentation
    summary: "The API Contracts table lists msgpack only for product routes"
    location: "project-documents/user/slices/231-slice.api-surface-coverage-futures-tick.md:499-502"
---

# Review: slice — slice 231

**Verdict:** CONCERNS
**Model:** claude-opus-5-5

## Findings

### [CONCERN] The products and schedule routes have unclear outage behaviour, and their connections are unbounded

`products` and `schedule` call `latest_active` / `build_schedule` with URLs, and those functions open their own connections (lines 187–193). The design never says whether these two routes go through the `tick_connection` dependency first. That leaves three gaps:

- **The behaviour contradicts itself.** TD5 (lines 390–392) says a per-product `latest_active` failure sets that line's `error`, which would be a 200. Walkthrough step 7 (line 682) and the API Contracts table (line 496) expect `503 unreachable` from `/futures/products`. A database that goes down mid-request on the product time-series routes has the same gap. TD2 (lines 275–276) maps the roll functions' `OperationalError` to 503, but nothing says how that fits with the per-line `error` rule.
- **A request can hang.** These per-request connections are outside the pool, so `TICK_API_CHECKOUT_TIMEOUT_SECONDS` does not bound them. Unless they have a connect timeout, an unreachable host can make a request wait for the OS TCP timeout before any 503. That defeats the closed-port test the Risk section relies on (lines 722–724).
- **The session budget and connection count leave them out.** The pool's `configure` (read-only, `statement_timeout`, `work_mem`, TD2) does not apply to these connections. The `--workers N` estimate of 16 connections at most (lines 762–765) does not count them, though every product request opens them on top of its pooled connection (lines 707–709).

Fix:
- State that every futures route, catalog routes included, runs `tick_connection` first, so not-configured, unreachable and migrations-pending all give a 503 before any roll call.
- Say which failures stay per-line `error` in `products` (refusals such as no held sessions) and which become a 503 (outages).
- Name the connect timeout the roll functions' connections use under the API.
- Add those connections to the `max_connections` budget.

### [CONCERN] A third row-ceiling style is added without extending 180's Range Policy

180's Range Policy records two ways to enforce the ceiling:

- 186 estimates before the query, and "no DB work is done for a rejected request".
- 188 takes an exact `count(*)` first, so the 422 quotes the true count.

TD4 adds a third: it stops during the read, so a refused request reads up to 85,000 rows, and the 422 never states the true count. The reasoning is good and backed by measurement (a 687k-tick session; a count would cost a full scan). But 188 added its own "Extended by slice 188" paragraph to 180 for exactly this kind of change. 231's document updates only mark 180's tick exclusion closed (lines 87–88, 745–746). Without a third paragraph, the serving architecture's "No DB work is done for a rejected request" becomes wrong for the futures routes. Fix: add an "Extended by slice 231" paragraph to 180's Range Policy to the document-update scope.

### [PASS] Slice plan scope is fully covered and nothing is overbuilt

The design covers everything in slice plan entry 12:

- ticks and bars by contract and by product plus rule;
- the catalog with definitions, expirations and the active contract per rule;
- coverage in `/status` and freshness in `/overview`;
- the `tick_db_url` seam, the 503 posture, and the health field;
- the TD8 answer per surface;
- typed response models;
- both reference documents and a regenerated `openapi.json`;
- closing 180's exclusion.

Nothing is dropped. The exclusions are justified: writes, `debug`, pagination, back-adjustment, caching, and production wiring (left to 234). Only one new read is introduced (`catalog_reads.contracts_for`). Everything else reuses 229 and 230.

### [PASS] The serving-layer failure posture matches the architecture

The design follows the architecture's "Serving-layer failure posture" closely:

- `open(wait=False)` with a startup failure logged, not raised;
- the pool's own reconnect, with no retry loop of ours;
- 503 in the existing `{"error": …}` shape;
- health always returns HTTP 200;
- equities and Kalshi routes never touch the tick pool, and a test asserts it.

It also keeps the architecture's split between *absent* and *unreachable*. Checking migrations per checkout lets a database migrated after startup recover without a restart. The read-only role, the DML-only credential and password scrubbing cover the secrets requirement.

### [PASS] The health-field vocabulary change is recorded

The architecture specifies `ok` or `error` for the health field. The slice uses 230's five-state `TickLineState` instead, gives the reason (it separates "not configured" from "down"), and plans a 220 Revision Log entry. The exclusive-end window convention is handled the same way: it is the recorded 230 departure, with stated exceptions in `reference.md` §2.2 and `agents.md` §3.

### [PASS] The active contract and roll rule are explicit on every response

The architecture requires every surface to name which contract served and which rule chose it, and the TD5 envelope meets this. It carries `target.rule`, `is_default`, `segments` with `raw_symbol`, plus `unresolved` and `incomplete`. Contract paths skip roll resolution. Prices are exact strings and there are no integers above 2^53 on the wire, so the "prices stay exact" principle holds through to JavaScript clients. Tests pin the model field sets to the `roll_payload` keys, so the CLI and API outputs cannot drift.

### [NOTE] `TICK_SOURCE_NAME` is imported from a CLI module

`api_server` importing from `cli/` points the dependency the wrong way. There is precedent: `routes/operations.py` already imports `build_overview` and `gather_db_facts` from `cli/commands/overview.py`, so this adds no new edge. The slice already moves `classify` into `data/tick/line_state.py`. Moving `TICK_SOURCE_NAME` into `data/tick/constants.py` at the same time would cost little and stop the coupling from growing.

### [NOTE] The `/status` latency risk has no target

The 220 architecture states no API latency requirement, so setting bounds from measurement meets the review criteria. But `/api/v1/status` already misses its load bound (2.79 s against 1.5 s), and the slice adds `accounting_lines` to it while saying the block will not be dropped. The design should say whether 231 accepts a slower `/status` as known debt or makes the tick block's cost a pass/fail item in the load tier.

### [NOTE] The API Contracts table lists msgpack only for product routes

TD1 gives the contract `ticks` and `bars` routes `format=` too, but the table marks "(json or msgpack)" only on the two product routes. The table should match TD1 so the reference documents are written correctly.

### Run Digest

- Response length: 8408 chars
- Response is newline-free: no
- Tool calls made: 9
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 67.2 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
