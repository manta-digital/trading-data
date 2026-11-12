---
docType: review
layer: project
reviewType: code
slice: tick-bars-and-compact-tick-serving
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: CONCERNS
verdictSource: derived
sourceDocument: project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md
aiModel: deepseek/deepseek-v4.1-flash
aiProfile: openrouter
status: complete
dateCreated: 20261112
dateUpdated: 20261112
reviewedSha: 9c38201dd36cca23ea5c60ade4b33bda949b34f6
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 35
diffTruncated: false
turns: 20
promptTokens: 2466447
cachedTokens: 2042112
completionTokens: 137445
reasoningTokens: 132761
durationSeconds: 845.3
costUsd: 0.137315
costBasis: alias_pricing
runId: run-20261112-p6-3080c8dd
squadronVersion: 0.24.26
categoriesNormalized: true
findings:
  - id: F001
    severity: concern
    category: documentation-drift
    summary: "`docs/api/reference.md` §7 says \"three routes accept `format=msgpack`\" — the artifact now declares nine, two of them added by this slice"
    location: "docs/api/reference.md:2042"
  - id: F002
    severity: concern
    category: correctness
    summary: "The cutter guards an undefined price but not an undefined size, which would be summed into `volume` as a real figure"
    location: "src/manta_trading/data/tick/tick_bars.py:189"
  - id: F003
    severity: note
    category: consistency-rot
    summary: "`dbn_values.py` adds unannotated and partially annotated constants"
    location: "src/manta_trading/data/tick/dbn_values.py:14"
  - id: F004
    severity: note
    category: documentation-drift
    summary: "`read_tick_bars` mutates the cutter on the event loop, against the invariant its own docstring states"
    location: "src/manta_trading/data/tick/tick_bar_reads.py#read_tick_bars"
  - id: F005
    severity: pass
    category: other
    summary: "The rule is one module, pinned by isolation and an independent oracle, and admission is proved to run before any read"
    location: "test/unit/data/tick/test_tick_bars.py"
---

# Review: code — slice 308

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash
**Evidence:** 35 successful tool call(s) of 35 made; 167438 of 167438 diff characters reached the model

## Findings

### [CONCERN] `docs/api/reference.md` §7 says "three routes accept `format=msgpack`" — the artifact now declares nine, two of them added by this slice

§7 opens with "Three routes accept `format=msgpack` — equity bars (§3.2), Kalshi candlesticks (§5.1) and Kalshi trades (§5.2)" and repeats it later: "Both media types are declared on the `200` of all three routes". That is no longer true, and this slice widens the gap: `test/unit/api_server/test_openapi_artifact.py`'s `PUBLISHED_SCHEMA_PATHS` (which its own docstring calls "the routes that offer `format=msgpack` and so declare two media types") now lists the six `/api/v1/futures/*` time series, two of which — `/contracts/{symbol}/tick-bars` and `/products/{product}/tick-bars` — this slice adds, alongside `/api/v1/bars/{symbol}` and the two Kalshi routes, for nine. `timeseries_200` declares both media types on every one of them. The slice edits this same file (§2.7, §6.1, §6.9, §6.10) and re-counts the artifact elsewhere (`EXPECTED_ARTIFACT_PATH_COUNT` 25 → 27) without touching §7.

The same file's §2.7 carries a second specific number that this slice moves: "the eight hand-written `422`s in this codebase send `{"error": "…"}`". `futures_tick_bars._read` raises a new hand-written `422` for `TradeScanExceeded` and reuses `over_ceiling` for the tick-bar row ceiling, so the true count is now at least ten. `scripts/check_api_docs.py` cannot catch either claim — by its own §9 it checks markers and token sets, not prose — so both numbers will keep drifting unless they are dropped. Prefer "the futures time series and both Kalshi time series among them" over a count, and remove the `422` tally.

### [CONCERN] The cutter guards an undefined price but not an undefined size, which would be summed into `volume` as a real figure

`TickBarCutter._check` refuses `UNDEF_INT64` in `trade.price` and raises `TickBarInputError`, with the module docstring promising the exact opposite of a silent fallback: "anything else is a caller bug and raises :class:`TickBarInputError`, never a silent reorder". `size` gets no such check, and `_OpenBar.add` sums it unconditionally into `volume`, `buy_volume` and `sell_volume`:

```python
self.volume += size
...
if trade.side == TRADE_SIDE_BUY:
    self.buy_volume += size
```

`storage_columns.py` states explicitly that `tick_trade` stores provider sentinels "as delivered", naming `4294967295` as *the undefined size*, so the sentinel is a documented resident of the table this reader selects from. A row carrying it would produce a bar whose `volume` is inflated by 4,294,967,295 and served with HTTP 200 — the one class of corruption the price check exists to prevent, left open on the volume fields. The columnar `/ticks` body has the same asymmetry: `futures_ticks._defined` maps `UNDEF_INT64` to `None` for `price`, `bid_px` and `ask_px`, but `size`, `bid_sz` and `ask_sz` pass through raw.

I could not confirm that an undefined `size` actually occurs in a stored `trade` record (for `action=T` the provider documents size as required), which is why this is a concern rather than a failure — but the guard is the cheap half of the same check the code already makes for price, and the asymmetry means the failure mode would be a plausible-looking number rather than a loud `500`. Either refuse it in `_check` beside the price test, or comment why `size` cannot be undefined while `price` can.

### [NOTE] `dbn_values.py` adds unannotated and partially annotated constants

The new module declares `UNDEF_INT64 = 2**63 - 1` with no annotation at all and `TRADE_SIDE_BUY: Final = "B"` / `TRADE_SIDE_SELL: Final = "A"` with `Final` but no type parameter. The Python rules ask that class attributes be type hinted and use `Final[int]` / `Final[str]`. `UNDEF_INT64` is a straight move from `constants.py` (which had it unannotated too), so it is inherited style rather than a regression; the two side constants are new. `tick_bars.py` then compares `trade.side` against these strings, where the rules would prefer a `StrEnum` — `StrEnum` is standard library, so the "no database stack" constraint is not the obstacle, though converting provider strings at the boundary would have to be explicit.

### [NOTE] `read_tick_bars` mutates the cutter on the event loop, against the invariant its own docstring states

The module docstring says "Each fetched batch is pushed through it in the default executor … and awaited before the next fetch, so one thread at a time touches the cutter; the awaited future is the hand-off between the loop and the worker thread." After the loop, `read_tick_bars` calls `state.cutter.close_session()` — which sets `self._open = None` — directly on the loop thread, then `state.rows([...])`. This is safe as written, because no executor call is outstanding at that moment, but it means the mechanical invariant is "the cutter is touched by the loop only when no worker call is in flight" rather than "only the worker touches the cutter". A later change that overlaps the two (a speculative prefetch, say) would find the comment reassuring and the code unsynchronised. Either close the session inside the executor (`await _cut(loop, state, window, [])`-style) so the stated rule is literally true, or restate the invariant as the weaker one the code actually keeps.

### [PASS] The rule is one module, pinned by isolation and an independent oracle, and admission is proved to run before any read

`tick_bars.py` imports nothing beyond the standard library and `dbn_values`, and `test_tick_bars_isolation.py` makes that mechanical rather than aspirational: a fresh `python -I -c` subprocess asserts neither `psycopg` nor `manta_trading.data.tick.constants` is in `sys.modules` after the import. The rule itself is checked against `tick_bar_oracle.py`, a deliberately naive re-derivation (sort by key, `groupby` session, slice by N, summarise) over the committed 2024-12-03 `tbbo` cut and its derived `trades` twin, with the fixture's premise (`SESSION_COUNTS`, `BEFORE_CLOSE`/`AFTER_OPEN`) asserted so a changed fixture fails loudly instead of silently agreeing. Each guarantee the docs promise has its own test: `n == session count` is one full bar and `n` above it one partial, repeated `(ts_event, sequence)` records stay separate trades, `buy_volume + sell_volume + side-N volume == volume`, and the trades twin matches `tbbo` with the quotes nulled.

Admission is tested where it can actually break: `test_admission_over_the_limit_refuses_before_any_read` uses a recording reader and asserts `reads == []` on refusal, `test_over_the_trade_limit_is_422_before_any_tick_is_read` does the same through the route and then shows the same request succeeding at one session, and `test_until_stops_the_scan_early` counts `fetchmany` calls and then verifies every pooled connection is back to `IDLE` — so the early-stop path is shown not to leak a cursor or a checkout. `test_futures_ticks_columnar.py` round-trips both `/ticks` routes through JSON *and* msgpack against `read_ticks`' own rows, which is the right oracle for a re-encoding: it proves the wire form loses nothing but `bid_ct`/`ask_ct`.

### Run Digest

- Response length: 8490 chars
- Response is newline-free: no
- Tool calls made: 35
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 491349
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / cache write / completion / reasoning: 2466447 / 2042112 / not computed / 137445 / 132761
- Duration: 845.3 s
- Cost: $0.14 (alias_pricing)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 5
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 5
- Finding-shaped matches — surviving validation: 5

### Raw Response

## Summary

The slice is well-structured and its core risk areas are covered: the tick-bar rule lives in one stdlib-only module checked against an independent oracle, admission is measured from the ledger **before** any tick is read, the "whole session" contract is enforced mechanically via `TickWindow.clipped`, the early-stop at `until` is placed on a bar boundary (so it never truncates a bar), and the abandoned-client `499` path rolls back and answers bodiless without logging an error. The measurement script is now anchored to the repo (`Path(__file__).resolve().parents[1]`) and validates its numeric flags before any production request, which resolves the earlier review's note.

What remains is documentation that this slice contradicts (and grows), plus two narrower points: the cutter guards an undefined *price* but not an undefined *size*, and the reader's stated "only the worker thread touches the cutter" invariant is not what the code does.

## Findings

### [CONCERN] `docs/api/reference.md` §7 says "three routes accept `format=msgpack`" — the artifact now declares nine, two of them added by this slice
category: documentation-drift
location: docs/api/reference.md:2042

§7 opens with "Three routes accept `format=msgpack` — equity bars (§3.2), Kalshi candlesticks (§5.1) and Kalshi trades (§5.2)" and repeats it later: "Both media types are declared on the `200` of all three routes". That is no longer true, and this slice widens the gap: `test/unit/api_server/test_openapi_artifact.py`'s `PUBLISHED_SCHEMA_PATHS` (which its own docstring calls "the routes that offer `format=msgpack` and so declare two media types") now lists the six `/api/v1/futures/*` time series, two of which — `/contracts/{symbol}/tick-bars` and `/products/{product}/tick-bars` — this slice adds, alongside `/api/v1/bars/{symbol}` and the two Kalshi routes, for nine. `timeseries_200` declares both media types on every one of them. The slice edits this same file (§2.7, §6.1, §6.9, §6.10) and re-counts the artifact elsewhere (`EXPECTED_ARTIFACT_PATH_COUNT` 25 → 27) without touching §7.

The same file's §2.7 carries a second specific number that this slice moves: "the eight hand-written `422`s in this codebase send `{"error": "…"}`". `futures_tick_bars._read` raises a new hand-written `422` for `TradeScanExceeded` and reuses `over_ceiling` for the tick-bar row ceiling, so the true count is now at least ten. `scripts/check_api_docs.py` cannot catch either claim — by its own §9 it checks markers and token sets, not prose — so both numbers will keep drifting unless they are dropped. Prefer "the futures time series and both Kalshi time series among them" over a count, and remove the `422` tally.

### [CONCERN] The cutter guards an undefined price but not an undefined size, which would be summed into `volume` as a real figure
category: correctness
location: src/manta_trading/data/tick/tick_bars.py:189

`TickBarCutter._check` refuses `UNDEF_INT64` in `trade.price` and raises `TickBarInputError`, with the module docstring promising the exact opposite of a silent fallback: "anything else is a caller bug and raises :class:`TickBarInputError`, never a silent reorder". `size` gets no such check, and `_OpenBar.add` sums it unconditionally into `volume`, `buy_volume` and `sell_volume`:

```python
self.volume += size
...
if trade.side == TRADE_SIDE_BUY:
    self.buy_volume += size
```

`storage_columns.py` states explicitly that `tick_trade` stores provider sentinels "as delivered", naming `4294967295` as *the undefined size*, so the sentinel is a documented resident of the table this reader selects from. A row carrying it would produce a bar whose `volume` is inflated by 4,294,967,295 and served with HTTP 200 — the one class of corruption the price check exists to prevent, left open on the volume fields. The columnar `/ticks` body has the same asymmetry: `futures_ticks._defined` maps `UNDEF_INT64` to `None` for `price`, `bid_px` and `ask_px`, but `size`, `bid_sz` and `ask_sz` pass through raw.

I could not confirm that an undefined `size` actually occurs in a stored `trade` record (for `action=T` the provider documents size as required), which is why this is a concern rather than a failure — but the guard is the cheap half of the same check the code already makes for price, and the asymmetry means the failure mode would be a plausible-looking number rather than a loud `500`. Either refuse it in `_check` beside the price test, or comment why `size` cannot be undefined while `price` can.

### [NOTE] `dbn_values.py` adds unannotated and partially annotated constants
category: consistency-rot
location: src/manta_trading/data/tick/dbn_values.py:14

The new module declares `UNDEF_INT64 = 2**63 - 1` with no annotation at all and `TRADE_SIDE_BUY: Final = "B"` / `TRADE_SIDE_SELL: Final = "A"` with `Final` but no type parameter. The Python rules ask that class attributes be type hinted and use `Final[int]` / `Final[str]`. `UNDEF_INT64` is a straight move from `constants.py` (which had it unannotated too), so it is inherited style rather than a regression; the two side constants are new. `tick_bars.py` then compares `trade.side` against these strings, where the rules would prefer a `StrEnum` — `StrEnum` is standard library, so the "no database stack" constraint is not the obstacle, though converting provider strings at the boundary would have to be explicit.

### [NOTE] `read_tick_bars` mutates the cutter on the event loop, against the invariant its own docstring states
category: documentation-drift
location: src/manta_trading/data/tick/tick_bar_reads.py#read_tick_bars

The module docstring says "Each fetched batch is pushed through it in the default executor … and awaited before the next fetch, so one thread at a time touches the cutter; the awaited future is the hand-off between the loop and the worker thread." After the loop, `read_tick_bars` calls `state.cutter.close_session()` — which sets `self._open = None` — directly on the loop thread, then `state.rows([...])`. This is safe as written, because no executor call is outstanding at that moment, but it means the mechanical invariant is "the cutter is touched by the loop only when no worker call is in flight" rather than "only the worker touches the cutter". A later change that overlaps the two (a speculative prefetch, say) would find the comment reassuring and the code unsynchronised. Either close the session inside the executor (`await _cut(loop, state, window, [])`-style) so the stated rule is literally true, or restate the invariant as the weaker one the code actually keeps.

### [PASS] The rule is one module, pinned by isolation and an independent oracle, and admission is proved to run before any read
category: other
location: test/unit/data/tick/test_tick_bars.py

`tick_bars.py` imports nothing beyond the standard library and `dbn_values`, and `test_tick_bars_isolation.py` makes that mechanical rather than aspirational: a fresh `python -I -c` subprocess asserts neither `psycopg` nor `manta_trading.data.tick.constants` is in `sys.modules` after the import. The rule itself is checked against `tick_bar_oracle.py`, a deliberately naive re-derivation (sort by key, `groupby` session, slice by N, summarise) over the committed 2024-12-03 `tbbo` cut and its derived `trades` twin, with the fixture's premise (`SESSION_COUNTS`, `BEFORE_CLOSE`/`AFTER_OPEN`) asserted so a changed fixture fails loudly instead of silently agreeing. Each guarantee the docs promise has its own test: `n == session count` is one full bar and `n` above it one partial, repeated `(ts_event, sequence)` records stay separate trades, `buy_volume + sell_volume + side-N volume == volume`, and the trades twin matches `tbbo` with the quotes nulled.

Admission is tested where it can actually break: `test_admission_over_the_limit_refuses_before_any_read` uses a recording reader and asserts `reads == []` on refusal, `test_over_the_trade_limit_is_422_before_any_tick_is_read` does the same through the route and then shows the same request succeeding at one session, and `test_until_stops_the_scan_early` counts `fetchmany` calls and then verifies every pooled connection is back to `IDLE` — so the early-stop path is shown not to leak a cursor or a checkout. `test_futures_ticks_columnar.py` round-trips both `/ticks` routes through JSON *and* msgpack against `read_ticks`' own rows, which is the right oracle for a re-encoding: it proves the wire form loses nothing but `bid_ct`/`ask_ct`.

## Resolution

Premises checked against the merged code; all four held.

- **F001 (concern): fixed.** `reference.md` §7 now says every time-series route accepts `format=msgpack` instead of counting three; the "eight hand-written `422`s" tally is dropped from `reference.md` §2.7 and `agents.md`. `check_api_docs.py` passes.
- **F002 (concern): fixed in the cutter.** `UNDEF_UINT32` joins `dbn_values`; an undefined size raises `TickBarInputError` like an undefined price, with a test. The `/ticks` body keeps `size` as delivered (wire semantics unchanged; not required by this slice).
- **F003 (note): fixed.** The DBN constants are `Final[int]` / `Final[str]`. A `StrEnum` for the side is not adopted: the cutter compares provider strings at one boundary.
- **F004 (note): fixed by documenting.** The reader's docstring now states the real invariant (the closing `close_session` runs on the loop with no executor call in flight) and what a future overlap must change.
