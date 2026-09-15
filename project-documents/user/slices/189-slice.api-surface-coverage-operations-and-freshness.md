---
docType: slice-design
slice: api-surface-coverage-operations-and-freshness
project: trading-data
parent: user/architecture/180-slices.data-serving-api.md
dependencies: [188, 922]
interfaces: []
effort: 2
dateCreated: 20260913
dateUpdated: 20260913
status: not_started
---

# Slice Design: API surface coverage — operations and freshness (189)

## Overview

Slice 922 made pass runs a persisted fact and built `mt data overview` over
them: one screen answering the Project Manager's three routine questions —
what is running now and how far along, what ran last and how it ended, what
is fresh. It shipped to the CLI only. An operator away from a shell, or any
non-Python consumer, cannot ask those questions.

`GET /api/v1/status` predates all of it. Slice 185 built it over the
slice-167 `data_status` accessors, so it answers a *different* question:
per-symbol data health rows plus coverage-cagg freshness verdicts. It knows
nothing about `pass_runs`, which did not exist when it was written. There is
no overlap to reconcile — the two surfaces do not disagree, they simply do
not intersect.

This slice adds two read-only endpoints over facts that already exist:
`/api/v1/overview` (pass state, source freshness, health verdict, universe
accounting line) and `/api/v1/credits` (the EODHD quota). Both reuse the
functions 922 already proved. No new state, no new derivation, no new
freshness logic.

## Value

- **Operator**: the three questions are answerable over HTTP, from a phone
  or a dashboard, not only from a shell on manta9000.
- **Consumers**: trading-ui (initiative 120) and any agent consumer can show
  "a minute pass is running, 412 of 1,100 symbols" instead of a bare bar
  response with no provenance.
- **Architectural**: `build_overview` becomes the single derivation for both
  the screen and the endpoint. The two cannot disagree about what "abandoned"
  or "next firing" means, because neither computes it.

## Technical Scope

**In scope**

1. `GET /api/v1/overview` — the pure `Overview` shape (922) as a Pydantic
   response model: per-pass running rows, last run, next firing, cadence;
   source freshness; health verdict; universe accounting line.
2. `GET /api/v1/credits` — the EODHD daily quota, on its own route (D2).
3. `api_server/models/operations.py` — the response models.
4. `api_server/routes/operations.py` — both routes, thin per 188 D9.
5. Reuse of `gather`'s DB reads via a decomposition that separates the DB
   facts from the credit call (D3) — the API calls the same functions the
   CLI does, not copies of them.
6. Unit tests over the model translation (no DB), integration tests over
   both routes, and two load-tier bounds per 187 D10: latency on
   `/api/v1/overview`, and the executor-contention bound D8 requires because
   `/credits` is the API's first third-party-backed route.
7. `README.md` endpoint list, `app.py` description, regenerated
   `docs/api/openapi.json`.

**Out of scope**

- Any change to what passes record or how outcomes are decided — 921 and 922
  own that. This slice reads rows that already exist.
- Any change to `/api/v1/status` or `/api/v1/health`. Their committed shapes
  stay byte-identical, the property 188 SC1 established for equities paths.
- Writes of any kind. Both routes are reads; nothing here can start, stop, or
  acknowledge a pass.
- Streaming or push. A client that wants live progress polls.
- Retention or history. `latest_ended` is one row per kind, as on the screen;
  a run-history endpoint is future work (D7).
- The prose API reference — that is slice 190, which depends on this one.

## Dependencies

### Prerequisites

- **922**: `pass_runs` (migration 055), `PassRunRepository`, `PassKind`,
  `PassRunOutcome`, `build_overview`, `gather`, `read_source_freshness`,
  `overview_payload`, `firing_schedule`.
- **188**: the serving conventions this slice follows — `GATEWAY_TIMEOUT_RESPONSE`,
  `{"error": "…"}` bodies, thin routes over a reader module, the load tier.

### Interfaces required

| Symbol | Module | Use |
|---|---|---|
| `build_overview(facts, *, pid_alive=…)` | `cli/commands/overview.py` | The pure derivation. Called unchanged. |
| `OverviewFacts`, `Overview`, `PassLine`, `RunningRow`, `LastRun`, `SourceFreshness` | `cli/overview_types.py` | Leaf module, no I/O, already imported by both the command and the renderer. |
| `PassRunRepository.open_runs` / `.latest_ended` | `data/acquisition/pass_runs.py` | The two reads behind the PASSES block. |
| `read_source_freshness(conn)` | `cli/commands/overview.py` | The four `MAX(time)` probes. |
| `fetch_credit_usage(key)`, `CreditUsage` | `api/eodhd_account.py` | The credits route only. |
| `pid_is_alive` | `daemon/pass_run_recorder.py` | Abandoned detection — but see D4, it must not be the default here. |
| `get_db` | `api_server/deps.py` | Pooled connection. |

### Interfaces provided

- `GET /api/v1/overview` → `OverviewResponse`
- `GET /api/v1/credits` → `CreditsResponse`

Consumed by trading-ui (120) and by slice 190's reference documents.

## Technical Decisions

### D1 — A sibling `/api/v1/overview`, not an extension of `/api/v1/status`

Plan entry 9 left this open. Sibling, for three reasons:

1. **They answer different questions.** `/api/v1/status` is per-symbol data
   health, filtered by `symbol`, `health`, `granularity`, `all`. Overview is
   host-level process state. None of `/api/v1/status`'s four query params
   have any meaning applied to a pass run, and none of overview's content is
   filterable by symbol.
2. **`/api/v1/status` has a committed shape.** 186 committed `openapi.json`
   as a reviewable artifact and 188 SC1 held every pre-existing path
   byte-identical. Growing a large unrelated block into `StatusResponse`
   breaks that property for no gain.
3. **Different costs.** `/api/v1/status` pays coverage freshness probes;
   overview pays `2 × |PassKind|` indexed lookups plus four `MAX()` probes.
   Merging them makes every caller pay both.

The route name matches the CLI command (`mt data overview`), which is the
contract slice 190 will document.

### D2 — Credits are a separate endpoint, not a field on the overview

`gather()` fetches the EODHD credit line with an outbound HTTPS call. Every
other fact on the overview is a read on the pooled DB connection. Serving
them from one route would put a third-party dependency on the request path of
an endpoint a UI is expected to poll.

They differ on every axis that matters:

| | Overview facts | Credits |
|---|---|---|
| Source | Pooled DB connection | Outbound HTTPS to EODHD |
| Failure meaning | Nothing on the screen is true | One line is unavailable |
| Refresh rate | Per firing (minutes) | Daily counter, resets 00:00 UTC |
| Bound | `statement_timeout` | `EODHD_ACCOUNT_TIMEOUT_SECONDS` |

So: `GET /api/v1/credits` on its own. `/api/v1/overview` is pure DB, always
fast, safe to poll at any cadence. A client watching the quota — a real
operational concern, since normal nightly operation consumes most of the
daily allowance — asks for it on its own cadence and gets a first-class
failure field rather than a degraded sub-object.

**Consequence, stated plainly:** `/api/v1/overview` is *not* a strict
superset of `mt data overview --json`. The CLI payload's `credits` and
`credits_text` keys are absent from it, and live at `/api/v1/credits`. Slice
190's reference must say so, and the success criteria pin it (SC7).

### D3 — Split `gather` at the I/O boundary rather than duplicating it

`gather()` does two unlike things: it reads the DB, and it calls EODHD. The
API needs the first without the second; the credits route needs the second
without the first. Neither may be a copy — a copy is exactly how the screen
and the endpoint would come to disagree.

Extract `gather_db_facts(conn, settings, *, now=None, hostname=None,
pid_alive=…) -> OverviewFacts` from the existing body: everything up to and
including `facts.sources = read_source_freshness(conn)`. `gather()` keeps its
signature and becomes `gather_db_facts` plus the existing guarded credit
block, so every current caller — the command, its tests, the load tier —
is unaffected. The API route calls `gather_db_facts` and never the credit
path.

This is a refactor of ~20 lines with no behavior change, not new machinery.
The alternative (an API-local re-read of `pass_runs`) would put a second
definition of "what the overview reads" in the tree, which is the failure
mode this slice's plan entry explicitly set out to avoid.

**Import direction:** `api_server` → `cli` already exists (`routes/status.py`
imports `HealthStatus`; `models/responses.py` imports `StatusRow`). This
follows that precedent. `cli/overview_types.py` is a leaf module with no I/O
and no Typer dependency — the 922 review (F008) made it one precisely so both
sides could depend on it.

### D4 — `abandoned` is not computed by the API server

`build_overview` marks a running row `abandoned` when the row's hostname
matches the *local* host and `pid_is_alive(pid)` is false. That check is
sound on the CLI, which an operator runs on the machine in question. It is
**wrong** in the API server whenever the server and the passes are not the
same process namespace — a container, or a future split across hosts — where
a live pid on the pass host may or may not collide with an unrelated local
pid.

The API therefore passes `pid_alive=lambda _pid: True`: rows are reported as
running, never as abandoned. The response carries `hostname` and `pid` on
every running row, so a client can see *where* it is running, and the field
`abandoned` is always `false` from this endpoint.

Rather than publish a field that is structurally always false, the response
model **omits `abandoned`** and documents that abandonment detection is a
local-host judgment available from `mt data overview`. Publishing a field
whose value is a lie about a condition the endpoint cannot observe would be
worse than omitting it. A later slice that wants abandonment over HTTP needs
a liveness fact recorded *by the pass* (a heartbeat column), not a pid guess
by a reader — noted in D7.

**This is the one place the endpoint deliberately differs from the screen**,
and SC5 pins it.

### D5 — `next_firing` and `cadence` are served as computed, from the same schedule

`build_overview` computes both from `firing_schedule.schedule_for(kind,
minute_firing_days)` — no systemd call. The API serves them verbatim.

The minute cadence depends on `settings.minute_firing_days`, which `gather`
reads as a plain attribute (922 re-review F004: no `getattr` default, because
`weekdays=None` means *daily* to `FiringSchedule` and a default would render a
plausible wrong cadence instead of failing loudly). The API server's
`Settings` is the same object, so the same rule applies unchanged: read the
attribute, never default it.

A schedule that can never fire yields `next_firing: null` rather than failing
the response — `_pass_line` already catches that `RuntimeError`.

### D6 — Failure modes

| Condition | Response |
|---|---|
| DB unreachable / pool exhausted | 500 `{"error": "internal server error"}` via the existing app handler. Unlike the CLI's exit 2, no special-casing: nothing in the body would be true, and the global handler already logs the traceback. |
| `pass_runs` absent (pre-055 DB) | 200 with every pass reporting `running: []`, `last_run: null`. `gather`'s `_read` already degrades this way and logs one warning. An API server against an unmigrated DB should report "nothing recorded", not 500. |
| A source table absent (no Kalshi track) | 200; that source's `newest` is `null`. `_newest` already handles `UndefinedTable`. |
| Statement timeout (`/overview` only) | 504 via `GATEWAY_TIMEOUT_RESPONSE`, as every other DB route. Not declared on `/credits`, which issues no statement (D8). |
| EODHD unreachable (`/credits` only) | 200 with `credits: null` and `error: "<redacted reason>"`. A provider outage is a reported condition, not a server fault — the same contract `gather` already implements. The reason is redacted through `redact_token` before it reaches the body. |
| `MT_EODHD_API_KEY` unset (`/credits` only) | 200 with `credits: null` and `error` = `CREDITS_NO_KEY`. A setting, not a fault. |

No 404 and no 422 on either route: neither takes a path or query parameter.

### D7 — What is deliberately not served

- **Run history.** One `latest_ended` row per kind, as on the screen. A
  history endpoint over `pass_runs` (paginated, filterable by kind and
  outcome) is a reasonable later slice; it needs a retention answer first,
  which 922 explicitly deferred.
- **Abandonment over HTTP** (D4) — needs a heartbeat column written by the
  pass.
- **Any write.** No pass control surface. That is a different kind of API
  with a different auth posture, and the current posture is none (186 D-auth).

### D8 — `/credits` declares no `504`, and its load bound is about the shared executor, not its own latency

Two consequences of `/credits` being the API's first third-party-backed route,
both raised by the slice review (F001, F002).

**No `504`.** `GATEWAY_TIMEOUT_RESPONSE` is one shared constant whose
description reads "The database cancelled the query at the server's statement
timeout. Narrow the requested range or use a coarser granularity." `/credits`
issues no statement and takes no parameters, so declaring it would publish a
status the route never emits, with a remedy the caller cannot perform, into
the `openapi.json` that trading-ui and slice 190 consume. It is omitted.
`/api/v1/overview` keeps it — it does query the DB.

**A load bound after all, on a different quantity.** The earlier reading —
"no load bound; its latency is a third party's" — answered only the latency
half of the project's load-tier rule and missed the resource-bound half. The
rule is right and the omission was wrong:

`fetch_credit_usage` is a synchronous `httpx.get` bounded by
`EODHD_ACCOUNT_TIMEOUT_SECONDS` (5.0s), dispatched through
`run_in_executor(None, …)`. Every other route in this process does the same
(`bars.py`, `status.py`, `symbols.py`, `gaps.py`), and nothing anywhere sets
a custom executor — verified, no `set_default_executor` or
`ThreadPoolExecutor` in `api_server/`. So all of them share one default pool
of `min(32, cpu + 4)` threads. A handful of `/credits` calls arriving while
EODHD is slow can each hold a worker for the full five seconds, delaying
`/bars` and `/overview` requests queued behind them. That is precisely what
unit and integration tests cannot see.

The load tier therefore gets a **contention** assertion, not a latency one:
with `fetch_credit_usage` stubbed to block for the full timeout, concurrent
`/credits` requests must not push `/api/v1/overview` latency past its own
measured bound. The stub matters — the assertion is about this server's
thread budget, and must not depend on a third party's real response time or
consume live quota.

If that bound cannot be met, the fix is a small dedicated executor for the
credit call rather than a larger shared one, so a third party's slowness
cannot reach the DB routes at all. Deciding that belongs to implementation,
once the measurement exists.

## API Specification

Both `GET`, JSON, errors as `{"error": "…"}`.

`/api/v1/overview` declares `504` via `GATEWAY_TIMEOUT_RESPONSE`, as every
other DB route. **`/api/v1/credits` does not** (D8): it issues no statement,
so a statement timeout is not among its outcomes, and the shared constant's
description ("narrow the requested range or use a coarser granularity") names
a remedy for a route that takes no parameters.

```
GET /api/v1/overview
→ {
    "now": "2026-09-13T14:22:03Z",
    "passes": [
      {
        "pass": "minute",
        "cadence": "Mon,Thu 09:05 UTC",
        "running": [
          {"phase": "trailing", "done": 412, "total": 1100,
           "since": "2026-09-13T14:05:00Z",
           "progress_at": "2026-09-13T14:21:58Z",
           "hostname": "manta9000", "pid": 38214}
        ],
        "last_run": {
          "started_at": "2026-09-09T09:05:00Z",
          "ended_at": "2026-09-09T09:48:12Z",
          "outcome": "COMPLETE_QUOTA",
          "exit_code": 0,
          "detail": "1,100 symbols; stopped at provider quota"
        },
        "next_firing": "2026-09-16T09:05:00Z"
      },
      … one entry per PassKind: minute, daily, kalshi, health, accounting
    ],
    "sources": [
      {"name": "minute bars",    "newest": "2026-09-12T20:00:00Z"},
      {"name": "daily bars",     "newest": "2026-09-12T00:00:00Z"},
      {"name": "kalshi candles", "newest": "2026-09-13T14:21:00Z"},
      {"name": "kalshi trades",  "newest": "2026-09-13T14:21:47Z"}
    ],
    "health":   {"verdict": "OK — 1,100 symbols current", "at": "2026-09-13T14:00:11Z"},
    "universe": {"summary": "1,100 active / 43 exhausted / 12 still asking",
                 "at": "2026-09-13T03:12:44Z"}
  }

GET /api/v1/credits
→ {"credits": {"used": 98412, "daily_limit": 100000, "extra": 0, "remaining": 1588},
   "error": null}
  | {"credits": null, "error": "unavailable (MT_EODHD_API_KEY not configured)"}
  | {"credits": null, "error": "unavailable (timed out)"}
```

Field notes:

- `pass` values are `PassKind` (`minute`, `daily`, `kalshi`, `health`,
  `accounting`); `outcome` values are `PassRunOutcome` (`COMPLETE`,
  `COMPLETE_QUOTA`, `INCOMPLETE`, `PROVIDER_UNAVAILABLE`, `FAILED`). Both are
  served from the enums, never restated as literals — the rule 922 set for
  its own CHECK constraints.
- `running` is a list, not an object: two live runs of one kind is a real
  state the screen already renders (922 tests cover it).
- `done`/`total`/`phase` are `null` for a pass that reports no progress.
- `last_run` is `null` for a kind that has never completed a run
  (`NEVER_RUN` on the screen); `universe.summary` is `null` when no
  accounting pass has recorded one (`NO_ACCOUNTING`). The API serves `null`
  and leaves the prose to the client — the screen's sentinel strings are a
  rendering concern.
- `abandoned` is absent by D4.
- Timestamps are ISO-8601 UTC with `Z`, matching the 188 serialization
  convention (`mode="json"`, `+00:00` → `Z`).

## Data Flow

```
GET /api/v1/overview
  │
  ├─ get_db ──► pooled psycopg Connection
  │
  ├─ run_in_executor ──► gather_db_facts(conn, settings)      [D3]
  │                        ├─ PassRunRepository.open_runs(kind)     × 5
  │                        ├─ PassRunRepository.latest_ended(kind)  × 5
  │                        └─ read_source_freshness(conn)           4 × MAX()
  │                                  │
  │                                  ▼
  │                            OverviewFacts
  │                                  │
  ├─ build_overview(facts, pid_alive=lambda _: True)          [D4, pure]
  │                                  │
  │                                  ▼
  │                              Overview
  │                                  │
  └─ OverviewResponse.from_overview(overview) ──► JSON

GET /api/v1/credits
  │
  └─ run_in_executor ──► fetch_credit_usage(settings.eodhd_api_key)
                           guarded: exception ──► redact_token ──► error field
```

The DB reads run in an executor because `PassRunRepository` and the `MAX()`
probes are synchronous psycopg — the same async/sync bridge every other route
uses. The credit fetch is `httpx.get` (sync) and goes in an executor for the
same reason.

## Testing Strategy

**Unit** (no DB, no network) — the bulk of the value, because `build_overview`
is already pure and 922's test suite already covers the interesting states:

- `OverviewResponse.from_overview` over a constructed `Overview`: every field
  translated, `null`s preserved where 922's sentinels are.
- Two running rows of one kind survive as a two-element list.
- A pass with no `last_run` serves `null`, not a sentinel string.
- `abandoned` appears nowhere in the serialized body (D4).
- `pass` and `outcome` values match the enums exactly — a test that iterates
  `PassKind`/`PassRunOutcome` rather than listing tokens.
- `CreditsResponse` for all three shapes: usage, no key, fetch failure.
- A redacted failure reason carries no token substring.
- Route-level via `TestClient` with `gather_db_facts` patched.

**Integration** (real DB):

- Both routes 200 against a database with migration 055 applied.
- `/api/v1/overview` against a DB **without** `pass_runs`: 200, all passes
  empty (D6). This is the degradation that must not become a 500.
- An inserted open run appears in `running` with its phase and progress; the
  same run closed appears in `last_run` with its outcome.
- `gather_db_facts` and `gather` return equal `OverviewFacts` for the DB
  fields on the same connection — the guard that D3's split did not drift.

**Load** (per 187 D10, the tier 188 extended):

- `/api/v1/overview` latency bound. Measure first, then write the bound —
  never invent one. The read is `2 × 5` indexed lookups plus four `MAX()`
  probes; the `MAX()` probes over `kalshi.trades` (345M rows) and
  `kalshi.candlesticks` (240M rows) are the only plausible cost and are
  index-backed, so this is expected to be fast, but the number goes in the
  design after it is observed, not before.

  **Measured 2026-09-15** against `prod_shaped_db`: median **0.027 s** over
  ten sequential requests, range 0.023–0.103 s (the high sample is the first,
  cold — unwarmed pool, unprimed plan cache). A second run on the committed
  test measured a median of **0.055 s**. **Bound: 0.25 s** — roughly 9× the
  first median and 2.4× the worst cold sample. Deliberately loose, in the same
  way 187's symbol-detail bound is: it exists to catch a *shape* regression —
  an unbounded scan, a round trip per pass kind, a derivation moved back into
  the route — not to pin a millisecond.
- No unbounded scan: plan inspection on the four source probes.

- **Executor contention (D8).** With `fetch_credit_usage` stubbed to block for
  the full `EODHD_ACCOUNT_TIMEOUT_SECONDS`, concurrent `/api/v1/credits`
  requests must not push `/api/v1/overview` past its measured bound. The stub
  is required: the assertion is about this server's shared thread budget, and
  must not depend on EODHD's real latency or spend live quota.

  **This bound failed on first measurement, and the failure was real.**
  Measured 2026-09-15 with 36 concurrent stubbed credit calls against the
  default pool of 32 threads (`min(32, cpu + 4)` on this host), the first
  `/api/v1/overview` request took **4.564 s** — it queued behind a blocked
  thread and was released only when a credit call hit its timeout. Subsequent
  samples were fast (0.022–0.061 s) because those 36 calls all completed at
  ~5 s and freed the pool.

  Two consequences, both acted on:

  1. **The assertion is on the worst sample, not the median.** A median of
     0.023 s over those five samples passes a 0.25 s bound while an operator
     waits four and a half seconds. Contention is intermittent by nature — a
     request either finds a free thread or waits for a blocked one — so the
     median is precisely the statistic that hides it. Asserting `max(samples)`
     is what makes this test able to fail for its real reason.
  2. **The credit fetch got a dedicated executor**, which is the remedy D8
     names. It dispatches on a two-thread `ThreadPoolExecutor` instead of
     `run_in_executor(None, …)`, so however many credit requests arrive they
     can occupy only those threads and a database route never waits on one.
     Widening the shared pool was rejected as D8 requires: any bound on a
     shared pool is one a stuck provider can exhaust, so that would move the
     cliff to a higher concurrency rather than remove it. The bound was not
     relaxed.

`/api/v1/credits` gets no bound on its *own* latency — that is a third
party's. The bound above is on what it can do to everything else.

## Success Criteria

1. **SC1** — `GET /api/v1/overview` returns 200 with every `PassKind`
   represented, against production, with pass state matching what
   `mt data overview` prints at the same moment.
2. **SC2** — The endpoint's derivation is `build_overview`, called unmodified.
   Pinned by a test, not by inspection: `routes/operations.py` imports
   `build_overview` and the module contains no `next_firing_at`,
   `schedule_for`, `pid_is_alive` or `PassRunRepository` reference of its
   own. A grep is evidence that decays the moment someone adds a line; the
   test keeps holding after the slice closes.
3. **SC3** — The committed `openapi.json` gains exactly two paths and loses
   none, and every pre-existing path's schema entry is byte-identical (the
   188 SC1 property, which is schema-scoped). Verified at both levels: the
   schema by the artifact test, and `/api/v1/status` and `/api/v1/health`
   response *bodies* by a live before/after diff — this slice's `gather`
   split (D3) is upstream of neither route, and the diff is what proves it.
4. **SC4** — Against a DB with no `pass_runs` table, `/api/v1/overview`
   returns 200 with all passes empty and the sources block intact — not 500.
5. **SC5** — No `abandoned` field appears in the response body or in
   `openapi.json` (D4), and the API passes a constant-true `pid_alive`.
6. **SC6** — `GET /api/v1/credits` returns a populated `credits` object with a
   key configured; returns 200 with `credits: null` and a redacted `error`
   when the fetch fails; returns 200 with the no-key message when the key is
   unset. No token appears in any of the three bodies.
7. **SC7** — `/api/v1/overview` carries no `credits` field (D2), and the
   README and `app.py` description both say where credits live.
8. **SC8** — `pass` and `outcome` token sets in `openapi.json` equal
   `PassKind` and `PassRunOutcome` exactly.
9. **SC9** — Both routes publish a 200 schema in `openapi.json` (they return
   models directly, not `response_class=Response` — the trap 188 recorded for
   the time-series routes and left to slice 190).
10. **SC10** — Load bound on `/api/v1/overview` measured, written into this
    design, and passing.
11. **SC11** — `openapi.json` declares no `504` on `/api/v1/credits` (D8),
    while `/api/v1/overview` declares one.
12. **SC12** — With the credit fetch stubbed to block for the full timeout,
    concurrent `/api/v1/credits` requests leave `/api/v1/overview` within its
    SC10 bound (D8). If the bound cannot be met, a dedicated executor for the
    credit call is implemented and the design records the measurement that
    forced it.

## Verification Walkthrough

Read-only against production unless noted. `mt serve` on a local port, as in
188's walkthrough.

**1. Start the server.**
```
mt serve --host 127.0.0.1 --port 8123
```

**2. The three questions, over HTTP.**
```
curl -s localhost:8123/api/v1/overview | jq '.passes[] | {pass, cadence, running: (.running|length), last: .last_run.outcome, next: .next_firing}'
```
Expect one object per `PassKind`, five in all.

**3. The endpoint agrees with the screen.** Run both within a few seconds:
```
mt data overview
curl -s localhost:8123/api/v1/overview | jq .
```
Every pass's last outcome, next firing and cadence must match. (SC1)

**4. Against the CLI's own JSON**, confirming the only differences are the
documented ones:
```
mt data overview --json | jq 'del(.credits, .credits_text) | .passes |= map(.running |= map(del(.abandoned)))' > /tmp/cli.json
curl -s localhost:8123/api/v1/overview | jq . > /tmp/api.json
diff <(jq -S . /tmp/cli.json) <(jq -S . /tmp/api.json)
```
Differences confined to `now` and any pass that advanced between the two
reads. (SC5, SC7)

**5. A run in flight.** With a minute pass running (or a row inserted on a
disposable DB), confirm `running[0].phase`, `.done`, `.total`, `.hostname`,
`.pid` are populated and `abandoned` is absent:
```
curl -s localhost:8123/api/v1/overview | jq '.passes[] | select(.running|length>0) | .running'
```

**6. Credits.**
```
curl -s localhost:8123/api/v1/credits | jq .
```
Then with the key unset in the server's environment, confirm 200 with
`credits: null` and the no-key message — not a 500. (SC6)

**7. The pre-055 degradation.** On a disposable database without `pass_runs`:
```
curl -s localhost:8123/api/v1/overview | jq '{passes: [.passes[] | {pass, running: (.running|length), last_run}], sources}'
```
Expect 200, every `running` empty, every `last_run` null, `sources` still
populated. (SC4)

**8. The committed paths are unchanged.** Path-by-path comparison of
`openapi.json` before and after, as 188 did:
```
git show HEAD:docs/api/openapi.json | jq -S '.paths | keys' > /tmp/before.json
curl -s localhost:8123/openapi.json | jq -S '.paths | keys' > /tmp/after.json
diff /tmp/before.json /tmp/after.json
```
Expect exactly two additions, no removals. Then confirm both new paths carry a
200 schema, and that only `/api/v1/overview` declares a `504`:
```
curl -s localhost:8123/openapi.json | jq '{overview: (.paths["/api/v1/overview"].get.responses|keys), credits: (.paths["/api/v1/credits"].get.responses|keys)}'
```
Expect `504` present under `overview` and absent under `credits`.
(SC3 schema level, SC9, SC11)

**9. The pre-existing routes still answer the same shape.** Re-issue the two
requests captured before implementation began and compare structure — same
keys, same nesting, same types:
```
curl -s localhost:8123/api/v1/health | jq -S 'paths(scalars) | join(".")' > /tmp/health.after
diff /tmp/health.before /tmp/health.after
```
Same for the recorded `/api/v1/status` request. Values may move between
captures (freshness verdicts, counts, timestamps); the *shape* must not.
Nothing in this slice touches either route, so a shape difference means the
`gather` split reached further than D3 intended. (SC3 response level)

**10. The derivation is not duplicated.** Confirm `routes/operations.py`
reaches for `build_overview` and for none of the four symbols a second
derivation would need:
```
grep -n "build_overview" src/manta_trading/api_server/routes/operations.py
grep -nE "next_firing_at|schedule_for|pid_is_alive|PassRunRepository" src/manta_trading/api_server/routes/operations.py
```
Expect a hit on the first and no output from the second. The unit test pins
this permanently; the grep is the walkthrough's visible evidence. (SC2)

**11. Enum parity.**
```
curl -s localhost:8123/openapi.json | jq '.components.schemas | keys[] | select(test("PassKind|PassRunOutcome"))'
```
Token sets equal the Python enums. (SC8)

**12. Executor contention (D8).** Run the load tier's contention case, which
holds the credit fetch open for the full timeout while `/api/v1/overview` is
polled, and confirm overview latency stays within its SC10 bound. Not a
`curl` step — it needs the stub, so that a third party's real latency and the
live quota stay out of it. (SC12)

**13. Tiers.** Unit, integration, and load run separately (whole-`test/`
collection yields spurious errors). Known pre-existing failures on `main`
(`test_migration_051_052` ×2, `test_policy_advances_head` ×2) are confirmed
against `main` before being attributed anywhere.

### Evidence, walked 2026-09-15

Walked against production read-only with `mt serve --host 127.0.0.1 --port
8189`. Port 8189 rather than the 8123 written above; otherwise the commands
are as written except where a correction is recorded.

**Two corrections to the steps themselves** — both are the walkthrough being
wrong about reality, not the implementation working around it:

- **Step 4's literal `diff` cannot pass, and should not.** The CLI renders
  timestamps in local time (`-06:00`) and the API renders UTC with `Z`, which
  is the serialization convention this design specifies. Every line of the raw
  diff was that difference. The step must normalize both sides to UTC instants
  before comparing; with that done the *only* remaining difference is `now`,
  1.1 s apart — the gap between the two reads. Corrected command:

      mt data overview --json \
        | jq 'del(.credits, .credits_text)
              | .passes |= map(.running |= map(del(.abandoned)))' > /tmp/cli.json
      curl -s localhost:8189/api/v1/overview > /tmp/api.json
      # compare after normalizing every timestamp to an instant, not a string

- **Step 10's grep returns one hit, and must.** D4 requires a comment in
  `routes/operations.py` explaining why `pid_is_alive` is *not* used, so the
  raw `grep -nE "…|pid_is_alive|…"` matches that comment (line 64). "Expect no
  output" was wrong as written. The step must exclude comment lines:

      grep -nE "next_firing_at|schedule_for|pid_is_alive|PassRunRepository" \
        src/manta_trading/api_server/routes/operations.py | grep -vE ":\s*#"

  which returns no output. The unit test makes the same distinction properly,
  tokenizing comments and docstrings out before searching, and a companion
  test asserts the D4 comment still exists — so the guard cannot be satisfied
  by deleting the explanation.

**Step 2** — five pass lines, one per `PassKind`:

    {"pass":"minute","cadence":"13:05","running":0,"last":"COMPLETE_QUOTA","next":"2026-09-16T13:05:00Z"}
    {"pass":"daily","cadence":"00:35, 12:35","running":0,"last":"COMPLETE","next":"2026-09-16T00:35:00Z"}
    {"pass":"kalshi","cadence":"hourly :20","running":0,"last":"COMPLETE","next":"2026-09-15T17:20:00Z"}
    {"pass":"health","cadence":"hourly :50","running":0,"last":"COMPLETE","next":"2026-09-15T16:50:00Z"}
    {"pass":"accounting","cadence":"16:30","running":1,"last":"COMPLETE","next":"2026-09-16T16:30:00Z"}

**Steps 3 and 4 (SC1, SC5, SC7)** — after normalizing timestamps to UTC
instants, the CLI's `--json` payload and the endpoint differ in exactly one
field:

    .now: CLI=…897.007734  API=…898.152444

1.1 seconds, which is the interval between the two reads. Nothing else
differs: with `credits`/`credits_text` and `abandoned` deleted from the CLI
side, the two payloads are otherwise identical. The models have not drifted
from 922.

**Step 5** — a live `accounting` run was present during step 2
(`running: 1`) and had finished by the time step 5 was issued, so the captured
`running` array is from step 2's output above. The populated-row shape is
covered by `TestRunningAndLastRun` in the integration tier, which inserts an
open row and asserts `phase`, `done`, `total`, `hostname` and `pid` are
present and `abandoned` is absent.

**Step 6 (credits)** — against production:

    {"credits":{"used":99998,"daily_limit":100000,"extra":0,"remaining":2},"error":null}

The no-key case is asserted in both the unit and integration tiers rather
than by restarting the production server without its key. **Note for whoever
walks this next:** do not clear the key with `monkeypatch.delenv` — `Settings`
loads `.env`, so the variable is still set and the request goes to EODHD and
spends real quota. Override the settings object the route reads.

**Step 7 (SC4)** — the pre-055 degradation is covered by
`TestPre055Degradation` in the integration tier rather than by `curl`, since
it needs a database without `pass_runs` and the server points at production.
All three assertions pass: 200, every pass empty, `sources` still populated.

**Step 8 (SC3 schema level, SC9, SC11)** — exactly two additions, no removals:

    > "/api/v1/credits",
    > "/api/v1/overview",

and the status codes:

    {"overview":["200","504"],"credits":["200"]}

**Step 9 (SC3 response level)** — the two requests captured before any code
change (`/api/v1/health`, and
`/api/v1/status?symbol=AAPL&granularity=minute`) were re-issued and compared.
Both are identical in **shape and in value** — not merely the same structure
with moved numbers. Nothing this slice changed reached either route.

Recorded for the next walker: the status filter parameter is `symbol`
(singular). `symbols=` is silently ignored and returns the unfiltered
24,349-row body.

**Step 11 (SC8)** — published token sets equal the enums exactly:

    PassKind:       ["minute","daily","kalshi","health","accounting"]
    PassRunOutcome: ["COMPLETE","COMPLETE_QUOTA","INCOMPLETE","PROVIDER_UNAVAILABLE","FAILED"]

## Risks

1. **`gather` split drift (D3).** If a later change adds a DB read to
   `gather` rather than `gather_db_facts`, the endpoint silently loses it.
   Mitigated by the integration test asserting the two agree on the DB fields,
   which fails the moment they diverge.
2. **A third party reaching the DB routes.** `/credits` is the API's first
   route whose latency is set by someone else, and it shares the default
   executor with every DB route (D8). A slow EODHD could delay `/bars` and
   `/overview`. Mitigated by the contention bound (SC12), which fails the
   slice rather than shipping the coupling unmeasured; the dedicated-executor
   fix is identified and scoped if the bound cannot be met.
3. **Hostname semantics.** Every running row carries the hostname of the
   machine that ran the pass, which need not be the API server's. This is
   correct and intended, but a client rendering "running here" would be
   wrong. Mitigated by D4's omission of `abandoned` and by slice 190
   documenting the field.

## Implementation Notes

- `models/operations.py` and `routes/operations.py` stay under the ~300-line
  guidance. If the models file approaches it, split catalog-style as 188 did
  (`kalshi_catalog.py` / `kalshi_timeseries.py`) rather than growing one file.
- The `from_overview` translation should walk the dataclass fields where
  practical rather than restating them, the pattern `CandleRecord.from_row`
  used against `CANDLE_COLUMNS` in 188.
- `Response` must be imported at runtime, not under `TYPE_CHECKING`, if it is
  referenced at all — 188 recorded an unresolved-ForwardRef failure in
  FastAPI introspection tests from exactly that.
- Run `ruff format` scoped to touched files, then check `git diff main` for
  deletions before committing: scoping is necessary but not sufficient, since
  it rewrites whole files.

### Load-guard breakage evidence (Task 4.3)

A load test that cannot fail is not a test. Both bounds were deliberately
broken and the failure message checked before being restored.

**The contention bound broke on its own, which is the strongest evidence
available.** It was not a contrived breakage: written with a median-based
assertion, it *passed* while the first sample was 4.564 s. Changing the
assertion to `max(samples)` made it fail with

    overview-under-credit-contention: median=0.023s bound=0.250s
    samples=['4.564', '0.061', '0.023', '0.023', '0.022'] max=4.564s
    with 36 credit calls blocked on a pool of 32 threads. A third party's
    slowness is reaching a database route: give the credit fetch a dedicated
    executor (189 D8) rather than widening the shared pool or relaxing this
    bound.

which names the real cause — the shared executor — and the sanctioned remedy.
The dedicated executor was then implemented and the bound re-measured.

**`test_the_contention_fixture_actually_saturates`** guards the guard: it
fails if the chosen concurrency ever stops exceeding the executor width, which
is the condition under which the contention test would silently measure
nothing. Verified by asserting the inverse (concurrency 31 against this host's
32 threads):

    concurrency 31 does not exceed the 32-thread default pool: every call
    would get its own thread and nothing would contend

**The latency bound** was broken by lowering `_OVERVIEW_BOUND_S` below every
measured sample (0.010 s against a measured median of 0.055 s):

    overview: median=0.055s bound=0.010s
    samples=['0.076', '0.061', '0.055', '0.024', '0.037']

The message names the median, the bound and every sample, so a reader can tell
whether the endpoint regressed or the bound was wrong — which is the
distinction a bare "assertion failed" would lose.

**After the dedicated executor**, the contention case re-measured at
`max=0.062 s` against the same 0.25 s bound — down from 4.564 s, with the same
36-way concurrency. The coupling is gone rather than reduced.

One honest limitation of that re-measurement, recorded on the test itself:
`blocked_calls` fell from 36 to **2**. With the credit call confined to a
two-thread executor, the other 34 requests queue *outside* it and never enter
the stub. That is the fix working — the ceiling on what a stuck provider can
hold is the executor's width — but it means the latency assertion alone no
longer re-proves the default pool is saturated, and could in principle pass on
a fast machine for the wrong reason.

`test_the_shared_pool_is_not_what_serves_credits` closes that gap by asserting
the mechanism directly: it spies on `run_in_executor` and fails if the credit
fetch is ever dispatched on anything but its dedicated executor.

**This guard had to be written twice, which is worth recording.** The first
version searched the function's source text for `"run_in_executor(None"` and
for `"_credit_executor"`. It **passed with the executor reverted to `None`** —
the call spans two lines, so the needle never matched, and `_credit_executor`
still appeared in the function's own docstring. Both assertions were satisfied
by prose while the behavior was wrong. The rewritten test observes the actual
dispatch and fails with:

    AssertionError: the credit fetch dispatched on None rather than its
    dedicated executor. run_in_executor(None, …) shares the pool every DB
    route uses, which is the 4.564 s coupling 189 D8 exists to prevent.

The general lesson, and the reason the SC2 guard in the unit tier tokenizes
comments out rather than grepping: a test that reads source text is testing
the prose, and prose can agree with a test while disagreeing with the code.
