---
docType: slice-design
slice: api-reference-documentation-one-for-humans-one-for-agents
project: trading-data
parent: user/architecture/180-slices.data-serving-api.md
dependencies: [189]
interfaces: []
effort: 2
dateCreated: 20260916
dateUpdated: 20260916
status: not_started
---

# Slice Design: API reference documentation — one for humans, one for agents (190)

## Overview

Seventeen routes serve equity bars, the Kalshi catalog and tape, and
operations state. A consumer who wants to use them has three sources, none of
which is a reference:

1. **`docs/api/openapi.json`** (slice 186 D7) — machine-readable, committed,
   drift-gated. But **three routes publish no `200` schema at all**:
   `/api/v1/bars/{symbol}`, `/api/v1/kalshi/markets/{ticker}/candlesticks`,
   `/api/v1/kalshi/markets/{ticker}/trades` all use `response_class=Response`,
   so `BarsResponse`, `CandlesResponse`, `TradesResponse` and their record
   types are absent from `components/schemas`. The API's three highest-volume
   surfaces are the three with no published body type.
2. **`README.md` lines 554–774** — genuinely good prose: the four meanings of
   an empty Kalshi result, the `available` asymmetry, the error-shape table,
   the inclusive-window rule, the range cap and its span table, the auth/CORS
   posture. But it is an *endpoint list with essays attached*, not a
   reference. No per-field tables, no request/response examples, no `curl`.
3. **The source** — the field semantics are documented, carefully, in Python
   docstrings and comments that no consumer ever reads. `StatusRowRecord`
   records that `gap_count` counts open gaps only and that `health STALE`
   changed meaning in slice 922. `CoverageVerdict` records that `lag_seconds`
   is a float and that `None` is not `0.0`. `window_end_utc` records the
   production defect that made the day-end inclusive. All invisible on the
   wire.

The one piece of field-level documentation that *was* written for consumers —
seven `Field(description=…)` strings on `CandlesResponse` and `TradesResponse`
— sits on two of the three routes that suppress the schema, so it is stripped
from `openapi.json` entirely.

This slice writes the two documents the plan entry names, and — the part that
makes them worth writing — binds each to a mechanical check so neither can go
quietly wrong the way the FastAPI `description` did.

**A correction to this slice's own plan entry.** Entry 10 says the description
"reads *Data serving API for OHLCV bars, symbol metadata, and gap status* —
already wrong by omission once 188/189 land." Slices 188 and 189 updated it;
`app.py:148-155` now covers Kalshi and operations, and the committed artifact
matches. The premise held when the entry was written and no longer does. The
*lesson* still holds exactly: a hand-written string with nothing checking it
drifted, and was only noticed because a later slice happened to quote it.
That is the failure mode this slice exists to prevent at scale.

## Value

- **Consumers** (trading-ui, initiative 120): the three no-schema routes stop
  being reverse-engineering exercises. A UI author can read what `is_stale`
  means for `1m` (always `false`, by construction, not by luck) without
  reading `bars.py:182-193`.
- **Agent consumers**: an LLM asked "is the minute pass running?" can find
  `/api/v1/overview` by capability rather than by guessing route names, and
  knows that `count: 0` on `/api/v1/status` means *nothing wrong*, not *no
  such symbol* — the single most expensive misreading this API affords.
- **The project**: every semantic fact currently trapped in a docstring gets
  one consumer-facing home, and a check that fails when it stops being true.

## Technical Scope

**In scope**

1. `docs/api/reference.md` — the human reference (D1 settles the location).
2. `docs/api/agents.md` — the agent reference, capability-oriented (D2).
3. `scripts/check_api_docs.py` — the coverage/consistency gate (D4): every
   path in `openapi.json` is documented in both files; every documented path
   exists; every documented query parameter exists with the documented type;
   documented error-status codes match the declared ones.
4. `test/unit/api_server/test_api_docs.py` — runs that gate in the suite,
   alongside the existing artifact drift test.
5. The three suppressed response models published as schema components (D3) —
   the only code change in the slice, and it is additive: `BarsResponse`,
   `CandlesResponse`, `TradesResponse` gain a declared `200` without changing
   a single served byte.
6. Worked `curl` examples, executed against production and pasted with real
   output (D6), for every route.
7. README: the endpoint list becomes a pointer to the reference rather than a
   competing copy of it (D5).

**Out of scope**

- **Any change to a served response.** Not one byte of any body, header or
  status code changes. D3 adds a *declaration* of what is already sent; the
  drift test plus a byte-comparison of live responses before and after proves
  it (SC7).
- **New endpoints.** The two cross-cutting gaps stay where 188 put them:
  the unscoped Kalshi markets list and derived candle periods are Future Work
  items 4 and 5 in the plan.
- **Fixing the 422 shape mismatch.** OpenAPI declares `HTTPValidationError`
  for 422 on every route, but every hand-written 422 in this codebase sends
  `{"error": "…"}`. This slice **documents** the discrepancy (it is a real
  client trap) and does not fix it — the fix changes a published schema and
  belongs to its own slice (D7).
- **Auth, rate limiting, versioning policy.** The posture is recorded, not
  revisited.
- **A docs site, or publishing anywhere.** Two Markdown files in the repo.
- **`docs/api/foundation_usage_examples.md`.** Despite its path it documents
  slice-750 Python library modules, not the HTTP API. Untouched; the human
  reference states the distinction so the neighbouring file stops misleading.

## Dependencies

### Prerequisites

- **189**: `/api/v1/overview` and `/api/v1/credits` — the last two routes, so
  the surface is complete and a reference written now is not born partial.
- **186 D7**: `scripts/dump_openapi.py`, `ARTIFACT_PATH`, and the drift test.
  This slice's gate is a sibling of that one and reuses its loader pattern.
- **188**: `api_server/admission.py` (`admit_rows`, `not_found`) and the
  wire-message constants the error documentation quotes.

### Interfaces required

| Symbol | Module | Use |
|---|---|---|
| `create_app()` | `api_server/app.py` | The live schema, for the gate. |
| `ARTIFACT_PATH`, `generate()` | `scripts/dump_openapi.py` | Imported by path, as the existing artifact test does. |
| `API_MAX_BARS_PER_REQUEST` | `constants.py:1287` | The documented ceiling, read not retyped (D4). |
| `NARROW_FILTER`, `NARROW_WINDOW` | `api_server/admission.py:21,24` | Quoted remedy text. |
| `Granularity`, `HealthStatus`, `MarketStatus`, `PassKind`, `PassRunOutcome` | `constants.py`, Kalshi/ops modules | Enumerated vocabularies, read not retyped. |

### Interfaces provided

- `docs/api/reference.md` and `docs/api/agents.md` — consumed by trading-ui
  (120) and by any agent client.
- `scripts/check_api_docs.py --check` — a gate any later API slice runs.

## Technical Decisions

### D1 — Both documents live in `docs/api/`, beside the artifact they describe

Three candidates: `docs/api/`, `project-documents/user/reference/`, or the
README.

`docs/api/` wins. It already holds `openapi.json`, which both documents are
checked against — a reader who finds one finds all three, and the gate's
paths are siblings rather than a traversal across the repo.
`project-documents/user/` is the *project process* tree (architecture,
slices, tasks, reviews); it documents how the software is built, for the
people building it. A consumer of the HTTP API is not in that audience and
should not have to learn the `NNN-slice.` naming convention to find out what
`is_stale` means. The README stays an entry point (D5), not the reference —
it is already 850+ lines covering the daemon, Kalshi collection, backups and
the CLI, and a full API reference inside it would bury both.

Plain `reference.md` / `agents.md`, not `NNN-`prefixed: these are living
consumer documents regenerated against a moving artifact, not slice-indexed
process artifacts. `file-naming-conventions` governs `project-documents/`.

### D2 — The agent document is capability-indexed; the human document is route-indexed

The plan entry says "capability-oriented rather than route-oriented" for the
agent document, and the distinction is the whole point of writing two files
instead of one.

A human consumer arrives *knowing the route* — from `/docs`, from a stack
trace, from a colleague — and asks "what does this return and what can go
wrong?" Route-indexed, one section per path, is exactly right, and a
`curl`-and-output block answers the question fastest.

An agent consumer arrives *knowing the question* — "how far along is the
minute pass?", "what did SPY trade at last Tuesday?", "which Kalshi markets
resolved yes?" — and must find the route. Route-indexed prose forces it to
read all seventeen and infer. So `agents.md` is indexed by question:

```
### "Is anything running right now, and how far along?"
GET /api/v1/overview  → passes[].running[] — phase, done/total, since.
                        Empty list means nothing running for that pass kind.
No parameters. Pure DB read; safe to poll.
Not this: /api/v1/status answers per-symbol data health, not process state.
```

The `Not this:` line is deliberate and appears throughout. An agent's most
likely failure is a *plausible* wrong route — `/api/v1/status` for "is it
running", `/api/v1/health` for "is my data fresh", `/api/v1/gaps` for "does
this symbol exist". Each of those returns `200` with a confidently wrong
answer. Enumerating the near-misses is worth more than enumerating the hits.

Both documents describe one surface, so the facts must not fork. They share a
single source for anything derived — the ceiling, the enum vocabularies, the
remedy strings are read from code by the gate, not retyped in two places
where they can disagree.

### D3 — Publish the three suppressed schemas; this is the slice's only code change

`response_class=Response` on bars, candlesticks and trades is not incidental —
it is what permits `format=msgpack` to return `application/x-msgpack`
(`serialization.py:28`). But FastAPI reads the `200` schema from
`response_model=`, which those three routes never set, so the media-type
capability silently cost them their body type. `test_openapi_operations_schema.py:5-8`
already names this "the trap 188 recorded … leaves a UI generator with no type
to import."

Writing a reference for these three without fixing it means hand-transcribing
`BarsResponse` into Markdown — a second copy of a type definition, drifting
from the first the moment anyone adds a field. That is the exact failure this
slice exists to prevent, so the fix is in scope even though the slice is
nominally documentation.

The fix is a declaration, not a behavior change: add `responses={200: {...}}`
(or `response_model` with `response_class` retained) naming the existing model,
so the schema is published while the handler keeps returning a raw `Response`.
FastAPI does not validate or re-serialize what a raw `Response` returns, so the
bytes on the wire are unchanged — but that is a claim, not a fact, until
measured, so SC7 requires a byte comparison of live responses on all three
routes in both formats before and after. If any byte differs, the declaration
is wrong and the slice reverts to hand-written tables; the design does not
assume its own preferred outcome.

Two consequences worth stating: the seven `Field(description=…)` strings on
`CandlesResponse`/`TradesResponse` — the only field-level documentation in the
API — start reaching consumers for the first time. And `msgpack` responses
need the schema to say the body may also arrive as `application/x-msgpack`;
the `200` declaration lists both media types.

### D4 — The anti-drift mechanism is a coverage gate, not regeneration

The plan entry says both documents "regenerate or are re-verified at slice
close against the committed `openapi.json`." Regeneration is off the table:
generated prose is why the API has no useful documentation today — a
mechanically-produced route list is what `/docs` already gives you, and the
value here is exactly the part a generator cannot write (that `count: 0` on
`/api/v1/status` means *nothing wrong*, that the gaps route uses a
next-midnight-exclusive day end because of a 2004-01-01 SPY defect).

So: hand-written prose, mechanically checked for the properties a machine can
actually verify. `scripts/check_api_docs.py`, mirroring `dump_openapi.py`'s
shape (`--check` flag, exit 1 with a remedy line, no DB required):

| Check | Catches |
|---|---|
| Every path in `openapi.json` appears in both documents | A new route ships undocumented |
| Every path named in either document exists in the schema | A route is renamed or removed and the docs keep describing it |
| Every query parameter documented for a path exists on that path, with the documented type | A parameter is renamed, retyped, or dropped |
| Every parameter on a path is documented | A parameter is added silently |
| Documented error statuses ⊆ declared statuses for that path | The docs promise a `504` the route does not declare — `/api/v1/credits` deliberately has none |
| Values marked as code-sourced match the live constant | The ceiling changes from 75,000 and the prose does not |

The last row is how D2's "one source" is enforced. A documented value that
must track code carries an explicit marker naming the symbol, e.g.

```
The ceiling is 75,000 rows. <!-- from: manta_trading.constants.API_MAX_BARS_PER_REQUEST -->
```

The gate imports the symbol and compares. Enum vocabularies (`Granularity`,
`HealthStatus`, `MarketStatus`, `PassKind`, `PassRunOutcome`) are checked the
same way, as sets, so a new member fails the gate rather than quietly
extending a documented list. This is the project rule against scattering
comparison values across code, applied across the code/docs boundary.

**What the gate cannot check**, stated plainly because pretending otherwise
would be worse than the gap: whether a sentence of prose is *true*. "Raw
grains are never stale by construction" is verified by a test over `bars.py`,
not by this gate; the gate only knows the sentence mentions a path that
exists. Prose accuracy is the reviewer's job and the walkthrough's.

### D5 — The README endpoint list becomes a pointer, and its essays move

The README section (554–774) and the new reference would otherwise be two
descriptions of one surface, and the older one is not gated. Two copies of a
contract is the condition this slice is chartered to end.

The essays move to `docs/api/reference.md` in full — "Reading an empty Kalshi
result", "`available` semantics", "Error shapes", the inclusive-window rule,
the range cap and its span table. They are the best prose in the repo about
this API; they are moved, not rewritten, and the reference is their home.

What stays in the README: `mt serve` invocation, the `MT_API_*` environment
table (it belongs with the other environment tables), the one-paragraph
statement of what the API serves, the auth/CORS posture (a security-relevant
fact a reader must not have to follow a link to find), and a link to the
reference. The endpoint list itself goes — it is a seventeen-line index that
`reference.md` opens with, maintained in two places or in none.

Anchor links into the moved sections are rewritten, and the gate's
"documented path must exist" check makes a stale README endpoint impossible
to leave behind, since there are none left to go stale.

### D6 — Examples are executed against production and pasted verbatim, with a policy for their staleness

An invented example is a lie with syntax highlighting. Every `curl` block in
both documents is run against the live API and its real output pasted —
truncated where a body is long, with the truncation marked.

But real output embeds real timestamps and real prices, which are stale the
day after they are pasted, and the gate cannot detect that. So each example
block states the date it was captured, and the documents say once, at the
top, that example *values* are illustrative and example *shapes* are
contractual. A reader must never wonder whether `"volume": 74183200` is a
promise. Verification re-runs a sample and confirms shape, not values (SC8).

Examples use symbols and tickers that will still exist: `SPY` for equities, a
settled Kalshi market for the catalog and tape, so a reader re-running an
example gets output rather than a 404.

### D7 — The 422 shape mismatch is documented, not fixed

OpenAPI declares `HTTPValidationError` — FastAPI's `{"detail": [{"loc", "msg",
"type"}]}` — for 422 on every route. Every hand-written 422 in this codebase
sends `{"error": "…"}` through the `HTTPException` handler (`app.py:192`).
Both shapes really occur at 422: FastAPI's own request-validation failures
produce the declared one (deliberately unified with nothing —
`app.py:180-188`), and all eight hand-written refusals produce the other.

A generated client that types 422 from the schema will mis-parse eight of the
API's most informative messages, including both range-cap refusals. That is a
real trap and the reference documents it with both shapes and which
conditions produce which.

Fixing it changes a published schema, which is a client-visible contract
change — out of place in a slice whose defining constraint is that nothing
served changes (SC7). It becomes a Future Work entry in the plan.

### D8 — Failure modes

| Failure | Detection | Handling |
|---|---|---|
| A later slice adds a route and not its documentation | `check_api_docs.py`, in the suite | Gate fails with the undocumented path named |
| A parameter is renamed | Gate | Fails, naming path and parameter |
| The ceiling or an enum changes | Gate, via the `from:` markers | Fails, naming the symbol and both values |
| D3's declaration changes served bytes | SC7 byte comparison, pre/post | Revert D3; hand-written tables instead |
| Prose becomes untrue without a schema change | **Not detected** — stated in D4 | Reviewer and walkthrough |
| Example values age | Not detected, by design | Capture dates + the shape/value statement (D6) |
| The gate itself is bypassed | CI runs no test job (`test_openapi_artifact.py:6-7`) | Same exposure as the existing drift test; not this slice's to close |

## Deliverable Specification

### `docs/api/reference.md` — human reference

```
1. What this is, and what it is not          ← incl. the foundation_usage_examples
                                               disambiguation (D1/scope)
2. Quick start                                ← base URL, mt serve, /docs, curl
3. Conventions
   3.1 Time and time zones                    ← every *_ts/_time/_at is UTC;
                                               naive input read as UTC
   3.2 Date windows                           ← inclusive both ends (bars, Kalshi);
                                               the gaps exception and why
   3.3 Numbers                                ← _dollars/_fp are decimal STRINGS
                                               on the wire, and why
   3.4 Empty results                          ← the four meanings; 200-not-404
   3.5 The range cap                          ← 75,000 <!-- from: … -->;
                                               estimate vs. exact count;
                                               span table; rows-not-bytes
   3.6 Staleness                               ← which endpoints carry which
                                               signal; what to do about each
   3.7 Errors                                  ← 404/422/500/504 shapes,
                                               the two 422 shapes (D7)
   3.8 Auth, CORS, pagination                  ← the posture, stated
4. Endpoints                                   ← one section per path, 17 of them:
                                                 purpose · parameters · response
                                                 fields with units/nullability ·
                                                 errors · worked curl + output
5. Field glossary                              ← is_stale, coverage, gap_count,
                                                 lag_seconds, pass, count,
                                                 available, tape_filtered …
6. Keeping this accurate                       ← the gate, and how to run it
```

Field tables give name, type, nullability, **unit or time base**, and meaning.
That column is the reason this document exists: it is the one the source
docstrings have and the wire does not.

### `docs/api/agents.md` — agent reference

```
1. Orientation                     ← read-only; no auth; base URL; every
                                     response is JSON unless msgpack is asked
2. Capability index                ← "what questions can this answer",
                                     each with its route, its shape, and
                                     its Not-this: near-misses (D2)
3. Choosing a window               ← the cap, the span table, how to split
                                     a too-large request
4. Reading a result                ← 200-with-zero-rows is the normal case;
                                     the four meanings; how to tell
                                     "no data" from "wrong identifier"
5. Failure modes                   ← every status, what it means, whether
                                     retrying can help, what to change
6. Determinism and freshness       ← what is safe to poll (/overview is pure
                                     DB; /credits makes an outbound call),
                                     what can be stale and how you are told
7. Vocabularies                    ← the enum token sets <!-- from: … -->
```

Section 5 is the one that earns the separate file. An agent that cannot tell
"retry will not help, narrow the window" (422) from "retry might help, the
query was cancelled" (504) from "nothing is wrong, this symbol is healthy"
(200, `count: 0`) will loop, or give up, or report a false negative. Each
status gets an explicit retry verdict and the parameter change that resolves
it.

### `scripts/check_api_docs.py`

```sh
uv run python scripts/check_api_docs.py            # report
uv run python scripts/check_api_docs.py --check    # exit 1 on any failure
```

Reads the committed `openapi.json` (not the live app — the artifact is what
consumers hold, the same reasoning as `test_openapi_operations_schema.py:16-18`),
parses both documents' path and parameter mentions, resolves `from:` markers
by import. No database, so it runs on any checkout.

## Data Flow

```
                    source of truth
                          │
      ┌───────────────────┼────────────────────┐
      │                   │                    │
  route defs         constants &           handler
  (app.py)            enums                docstrings
      │                   │                    │
      ▼                   │                    ▼
 create_app().openapi()   │              (read by author,
      │                   │               not by machine)
      ▼                   │                    │
 docs/api/openapi.json    │                    │
      │                   │                    │
      │  ┌────────────────┘                    │
      ▼  ▼                                     ▼
  scripts/check_api_docs.py  ◄──────  reference.md, agents.md
      │                                        ▲
      │ paths · parameters · statuses          │
      │ · from:-marked values                  │
      ▼                                 hand-written prose
   pass / fail                          (examples executed
   (test/unit/api_server/                against prod, D6)
    test_api_docs.py)
```

The gate closes the loop the FastAPI `description` never had: a hand-written
string with code on one side and nothing on the other.

## Testing Strategy

**Unit** (`test/unit/api_server/test_api_docs.py`, no DB)

- Every path in the committed artifact is documented in both files.
- Every path named in either file exists in the artifact.
- Parameter coverage and types agree, both directions, per path.
- Documented error statuses are a subset of declared ones — with an explicit
  case for `/api/v1/credits`, which declares only `200`, so documenting a
  `504` there must fail.
- Every `from:` marker resolves and matches (ceiling, five enum token sets).
- The gate detects each drift class from a mutated fixture: an added path, a
  renamed parameter, a changed ceiling. **A gate not proven to fail is not a
  gate** — each case asserts failure, with the mutation reverted after.

**Unit, D3** (`test/unit/api_server/test_openapi_artifact.py`, extended)

- `BarsResponse`, `CandlesResponse`, `TradesResponse` and their record types
  are present in `components/schemas`.
- All three paths publish a `200` with content, listing both media types.
- The seven `Field(description=…)` strings appear in the artifact.

**Integration** (`test/integration/api_server/`)

- Every documented `curl` example, run against a real DB, returns the
  documented **shape**: status code, top-level keys, field types. Not values
  (D6).

**Manual, gating D3** (SC7)

- Byte-comparison of live responses on all three affected routes, `json` and
  `msgpack`, against the same running data, before and after the declaration.
  Identity is required; any difference reverts D3.

## Success Criteria

- **SC1** — `docs/api/reference.md` documents all 17 paths: purpose,
  every parameter, every response field with type, nullability and unit or
  time base, the errors each can return, and an executed example.
- **SC2** — `docs/api/agents.md` indexes the surface by question, with each
  capability naming its route and its plausible near-miss routes, and a
  failure-mode section giving every status a retry verdict.
- **SC3** — `scripts/check_api_docs.py --check` passes, and fails on each of
  the three mutations in the test.
- **SC4** — The gate runs in the unit tier and fails the suite on drift.
- **SC5** — Every value that must track code carries a `from:` marker
  resolved by the gate: the ceiling and all five enum vocabularies. No
  documented vocabulary is retyped.
- **SC6** — `BarsResponse`, `CandlesResponse` and `TradesResponse` appear in
  `components/schemas`; all three routes publish a `200` naming both media
  types; the regenerated artifact is committed and its drift test passes.
- **SC7** — Live responses on all three affected routes are **byte-identical**
  before and after D3, in both `json` and `msgpack`. No other route's
  schema entry changes (17 paths, diff confined to the three).
- **SC8** — Every example's shape is confirmed by an integration test; each
  example block carries its capture date; both documents state that values
  are illustrative and shapes are contractual.
- **SC9** — The README no longer carries a competing endpoint list; its
  moved sections are in the reference; `mt serve`, the `MT_API_*` table, the
  auth/CORS posture and a link to the reference remain; no dead anchors.
- **SC10** — Both documents state the 422 shape mismatch with both shapes and
  the conditions producing each; a Future Work entry records the fix.
- **SC11** — Slice plan entry 10 is materialized as **(190)**, and its stale
  premise about the FastAPI description is corrected in this design (done —
  Overview).

## Verification Walkthrough

Prerequisite: a running API. `mt serve` on the host with the DB configured;
these commands assume `http://localhost:8100`.

**1. The gate runs, and passes.**

```sh
uv run python scripts/check_api_docs.py --check
```

Expect: a pass line naming 17 paths checked.

**2. The gate actually fails.** Add a parameter to a route — say `limit` on
`/api/v1/gaps/{symbol}` — regenerate the artifact, and re-run:

```sh
uv run python scripts/dump_openapi.py
uv run python scripts/check_api_docs.py --check; echo "exit=$?"
```

Expect: `exit=1`, naming `/api/v1/gaps/{symbol}` and `limit` as undocumented.
Revert the route, regenerate, confirm the gate passes again. A gate that has
never been seen to fail has not been verified.

**3. The ceiling is not retyped.** Change `API_MAX_BARS_PER_REQUEST` to
`75_001`, re-run the gate.

Expect: `exit=1`, naming the symbol, the documented value and the live one.
Revert.

**4. The three routes now publish a body type.**

```sh
curl -s localhost:8100/openapi.json \
  | jq '.paths["/api/v1/bars/{symbol}"].responses["200"].content | keys'
curl -s localhost:8100/openapi.json \
  | jq '.components.schemas | keys | map(select(test("Bars|Candle|Trade")))'
```

Expect: both `application/json` and `application/x-msgpack`; and
`BarsResponse`, `BarRecord`, `CandlesResponse`, `CandleRecord`,
`CandleBidAsk`, `CandlePrice`, `TradesResponse`, `TradeRecord` present. Before
this slice, the first returns `null` and the second an empty list.

**5. Nothing served changed (SC7).** Before the D3 commit, capture:

```sh
for f in json msgpack; do
  curl -s "localhost:8100/api/v1/bars/SPY?granularity=1d&start=2024-01-02&end=2024-01-31&format=$f" \
    > /tmp/before.bars.$f
done
```

Repeat for candlesticks and trades on a settled market. After the commit,
capture the same into `/tmp/after.*` and:

```sh
for f in /tmp/before.*; do cmp "$f" "${f/before/after}" || echo "DIFF: $f"; done
```

Expect: no output. Any `DIFF` line means D3 changed behavior and must be
reverted.

**6. A human can answer a question from the reference alone.** Open
`docs/api/reference.md`, go to `/api/v1/bars/{symbol}`, and without opening
any source file answer: what time base is `timestamp`? What does `is_stale`
mean for `granularity=1m`, and why? What happens on a 20-year `1m` request?
Then run the section's example and confirm it returns the documented shape.

Expect: UTC, at the bar's opening instant; `false` always, because `1m` is a
raw hypertable with no cagg to be behind — not because it happens to be
fresh; a `422` naming the estimated bar count, the ceiling and the maximum
span in days for that granularity.

**7. The near-miss routes are named.** In `docs/api/agents.md`, look up "is
the minute pass running".

Expect: `/api/v1/overview`, with `/api/v1/status` explicitly named as the
wrong answer and why. Then confirm the trap is real and the warning earns its
place:

```sh
curl -s "localhost:8100/api/v1/status?symbol=SPY" | jq '{scope, count}'
```

Expect: `count: 0` for a healthy SPY — a confident, wrong-looking answer to
anyone who has not read section 4.

**8. Every documented status is one the route can return.**

```sh
curl -s -o /dev/null -w '%{http_code}\n' \
  "localhost:8100/api/v1/bars/SPY?granularity=1m&start=2004-01-01&end=2024-01-01"
curl -s -o /dev/null -w '%{http_code}\n' \
  "localhost:8100/api/v1/bars/NOSUCHSYM?granularity=1d&start=2024-01-02&end=2024-01-03"
curl -s "localhost:8100/api/v1/status?health=" | jq -r '.error'
```

Expect: `422`, `404`, and the empty-`health` remedy text — each matching the
reference's error section verbatim, including the remedy wording.

**9. `/api/v1/credits` has no `504`, and the documents do not claim one.**

```sh
jq '.paths["/api/v1/credits"].responses | keys' docs/api/openapi.json
grep -n "504" docs/api/reference.md | grep -i credits
```

Expect: `["200"]`, and no match — the route issues no statement, so a `504`
remedy would be false advice (189 D8).

**10. The README has one description, not two.**

```sh
grep -n "GET /api/v1" README.md
```

Expect: no endpoint-list matches; a link to `docs/api/reference.md` instead.
Confirm `mt serve`, the `MT_API_*` table and the auth/CORS paragraph are
still present.

**11. Full suite.**

```sh
uv run pytest test/unit -q
uv run pytest test/integration -q
```

Expect: unit green; integration green except the documented pre-existing
baseline failures.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| D3's declaration changes served bytes despite FastAPI not re-serializing a raw `Response` | Low | High — a silent contract change in a documentation slice | SC7 byte comparison is gating; failure reverts D3 to hand-written tables |
| The gate checks structure and lulls a reader into trusting prose | Medium | Medium | D4 states the limit explicitly, in the design and in both documents' "Keeping this accurate" section |
| Two documents describing one surface diverge | Medium | Medium | Single-sourced values via `from:` markers; both checked by the same gate in one run |
| Examples age into confusion | High | Low | Capture dates; the values-illustrative/shapes-contractual statement; integration tests assert shape |

## Implementation Notes

- **Write the gate before the prose.** It defines the markers the documents
  must carry; writing them afterwards means retrofitting seventeen sections.
- **D3 is its own commit**, with the SC7 comparison captured in the commit
  message. It is the only behavior-adjacent change here and must be
  independently revertible.
- Mine the docstrings systematically rather than by memory — `StatusRowRecord`
  (`models/responses.py:211-221`), `CoverageVerdict` (`:171-178`),
  `RunningRecord` (`models/operations.py:38-48`), `window_end_utc`
  (`routes/windows.py:28-34`), `MarketSettlement` (`models/kalshi_catalog.py:149-153`),
  `serialization.timeseries_response` (`:30-36`). Each holds a fact no
  consumer can currently see.
- `MarketRecord` deliberately uses Kalshi's field names verbatim and delegates
  their semantics to Kalshi (`models/kalshi_catalog.py:201-206`). The
  reference states that delegation and links out, rather than paraphrasing a
  vocabulary this project does not own.
- `/api/v1/status`'s `granularity` is `daily|minute`, a different vocabulary
  from bars' `Granularity`; `/api/v1/gaps` accepts `Granularity` and maps it
  to the family at `gaps.py:29`. Three related-looking parameters, two
  vocabularies — document it once, in Conventions, and cross-reference.
- The `pass` field on `PassLineRecord` is serialized by alias
  (`models/operations.py:81,87`) because `pass` is a Python keyword. Any
  Python consumer needs to know it cannot be an attribute name.
