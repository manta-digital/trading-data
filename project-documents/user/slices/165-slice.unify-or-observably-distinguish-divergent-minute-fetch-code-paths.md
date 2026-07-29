---
docType: slice-design
slice: 165-slice.unify-or-observably-distinguish-divergent-minute-fetch-code-paths
project: trading-data
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [162]
interfaces: []
dateCreated: 20260727
dateUpdated: 20260728
status: not_started
reviewFindings: [F001, F002, F003]
---

# Slice Design: Unify or Observably Distinguish Divergent Minute-Fetch Code Paths

## Overview

Two independently-implemented "fetch minute data for a symbol" code paths
exist today: `mt data pull 1m` (`run_minute_refetch`) and `mt data daemon
run --minute --symbols X` (`run_minute_cycle`). They accept overlapping
CLI arguments, touch the same tables, and emit similarly-shaped `INFO`
logs — but `run_minute_refetch` always falls back to the legacy
full-window `[history_start, target_end]` single-span seed, while
`run_minute_cycle` builds a coverage index and seeds only genuinely-missing
sessions (slice 162). Nothing observable distinguishes which algorithm ran.
This was discovered when two independent production-verification attempts
(slice 162, 2026-07-17) used `pull 1m` by mistake, following the slice's
own walkthrough, which had the same error.

This slice closes the defect at its root: `run_minute_refetch` is unified
onto coverage-aware seeding, so the two paths no longer diverge in the way
that burns credits and silently re-seeds decades of already-present data.
`force_reset_terminal` — the one dimension the two paths *should* differ
on (the operator escape valve for clearing terminal gap rows) — becomes a
fully orthogonal flag, exactly as `update_data_gaps` already treats it.
Every invocation additionally logs a `via=` marker so the algorithm that
ran is never in question again, independent of the unification.

## Value

Operator-facing: `mt data pull 1m --symbol X` becomes safe to use for
routine backfill/repair without the credit-burning full-window re-seed
risk. The interim mitigation doc (`user/reference/minute-fetch-code-paths.md`)
and its "use daemon run, not pull" workaround are no longer required —
both paths do the same coverage-aware seeding, differing only in
`force_reset_terminal` (operator-requested) and CLI scoping semantics
(single-shot vs. continuous/quota-governed).

Architectural: removes a concrete instance of the project rule violation
"never use user-accessible labels as logical structure" — the command
name no longer silently selects between materially different seeding
algorithms. The daemon/CLI sweep (scope item 2) either finds and resolves
further instances of the pattern or documents that none exist, closing
the open-ended risk the plan entry flagged.

## Technical Scope

**In scope:**
1. Unify `run_minute_refetch` onto coverage-aware seeding: build a
   **single-symbol** coverage index via a new
   `build_symbol_minute_coverage(conn, symbol)` in
   `data/gaps/minute_coverage.py` — identical query shape, staleness guard
   (`assert_cagg_fresh`, slice 168), timeout envelope, and fail-safe
   return-`None` contract as `build_minute_coverage_index`, but filtered
   `WHERE symbol = %s` — and pass the result into `_do_minute_symbol`, so
   its seed goes through `compute_missing_minute_sessions` exactly like the
   daemon path. Same return shape (`{symbol: set[date]}` for the one
   symbol), so `_do_minute_symbol` and `compute_missing_minute_sessions`
   need no changes. *(Amended 2026-07-28 — see Technical Decisions
   §Amendment; the original design reused the universe-wide index here.)*
   `force_reset_terminal`
   stays `True` by default for `run_minute_refetch` (unchanged operator
   semantics: still resets `PROVIDER_HOLE`/`RETRY_EXHAUSTED` before
   re-attempting) — but reset and seeding-algorithm are now independent:
   resetting terminal rows no longer implies a full-window reseed.
2. Add a `via` marker (`"refetch"` or `"cycle"`) to every `INFO`/`WARNING`/`ERROR`
   log line emitted from `_do_minute_symbol` and its callers, so log
   output alone identifies which entry point drove a given fetch. Threaded
   as a parameter, not inferred — no magic-string detection of caller identity.
3. Audit `src/manta_trading/cli/commands/data.py`,
   `src/manta_trading/data/acquisition/daemon/`, and
   `src/manta_trading/data/gaps/` for other near-identical-looking entry
   points that silently diverge in behavior. Findings recorded in this
   design's Audit Findings section below (already performed during design
   — see Technical Decisions).
4. Correct slice 162's Verification Walkthrough to use `mt data daemon run
   --minute --symbols ...` and cross-reference this slice (the walkthrough
   already carries a 2026-07-17 inline correction note; this slice
   converts that into the settled, permanent form and removes the
   "silently routes through a different code path" caveat since it is no
   longer true post-unification).
5. Retire `user/reference/minute-fetch-code-paths.md` — mark it superseded
   by this slice once both paths seed identically; its operator-guidance
   table's premise (avoid `pull 1m`) is no longer true.
6. Raise `MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT` from `30s` to `90s`
   (`constants.py`, one line). The 30s value was calibrated against slice
   162's measured ~3.05s / 2.4M-pair scan; the 2026-07-28 audit
   (`user/reference/prod-scale-and-coverage-scan-baseline.md`) measured
   18.46s / 22.7M pairs (9.4× backfill growth, not a regression) with a
   projected plateau of ~25–35s at full backfill — 90s covers the plateau
   plus concurrent-daemon-load variance permanently, because growth
   converges (history bounded at 2004, universe ~11.6k symbols).
   *(Added 2026-07-28.)*

**Out of scope:**
- Merging `run_minute_refetch` and `run_minute_cycle` into a single
  function. They still differ legitimately: `run_minute_cycle` iterates a
  symbol list under quota governance with `should_continue`/`on_symbol`
  hooks for the long-running daemon; `run_minute_refetch` is a single-symbol,
  single-shot operator command outside daemon quota, with `force_reset_terminal=True`
  and window-clamping defaults suited to a manual re-verify. Both already
  converge on `_do_minute_symbol` as shared implementation — that sharing
  is preserved, not restructured.
- Renaming `pull` or restructuring the CLI surface. Not needed once both
  paths seed identically — the "narrower scope explicit" alternative from
  the plan entry is superseded by the simpler fix (unify the algorithm).
- Changes to the daily fetch paths (`run_daily_cycle`/`run_daily_refetch`).
  Audited (see Technical Decisions) and found not to have the same defect
  class — daily's gap-recompute (`compute_missing_ranges`) is unconditional
  at the `update_data_gaps` layer regardless of caller, so `run_daily_refetch`
  cannot silently full-window-reseed the way `run_minute_refetch` could.
  Daily still gets the `via` log marker for consistency and because it costs
  nothing, but no behavior change.

## Dependencies

### Prerequisites
- Slice 162 (coverage-aware minute gap-seeding) — this slice unifies onto
  the coverage index and `compute_missing_minute_sessions` that 162 built.

### Interfaces Required
- `build_minute_coverage_index`, `compute_missing_minute_sessions`
  (`data/gaps/minute_coverage.py`, slice 162) — reused as-is, no signature
  changes.
- `update_data_gaps` (`data/gaps/update_data_gaps.py`) — `precomputed_ranges`
  parameter already exists and is exercised by `run_minute_cycle`; no
  changes needed there.

## Architecture

### Component Structure

All changes are within `src/manta_trading/data/acquisition/daemon/minute.py`
and `src/manta_trading/data/acquisition/daemon/daily.py` (log marker only),
plus doc updates. No new modules.

- `run_minute_refetch` (`minute.py`): gains a coverage-index build step —
  one extra `pool.connection()` block calling the new single-symbol
  `build_symbol_minute_coverage(conn, symbol)` — then passes the result to
  `_do_minute_symbol` alongside the existing `force_reset_terminal=True`.
  *(Amended 2026-07-28: was `build_minute_coverage_index`, the universe-wide
  scan `run_minute_cycle` uses.)*
- `build_symbol_minute_coverage` (`data/gaps/minute_coverage.py`, new):
  per-symbol sibling of `build_minute_coverage_index`. Same
  `assert_cagg_fresh` guard, same `SET LOCAL statement_timeout`
  (`MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT`), same catch-log-return-`None`
  fail-safe, same `{symbol: set[date]}` return shape — the only difference
  is `WHERE symbol = %s` (parameterized) and single-symbol output.
- `_do_minute_symbol` / `_process_minute_symbol`: gains a `via: str`
  keyword-only parameter, threaded into every log call already present in
  these functions (no new log call sites, existing ones gain the field).
- `_do_daily_symbol` / `_process_daily_symbol` (`daily.py`): same `via`
  threading for consistency; no algorithmic change. `run_daily_refetch`
  calls `_do_daily_symbol` directly (mirroring `run_minute_refetch`'s
  direct call to `_do_minute_symbol`) and must pass `via="refetch"` at
  that call site — `via` has no default, so this call site requires an
  explicit update, not just the two named functions above.

### Data Flow

Before (Path A — `run_minute_refetch`):
```
run_minute_refetch → _do_minute_symbol(coverage_index=None, force_reset_terminal=True)
  → _needs_seed=True → precomputed_ranges=None
  → update_data_gaps(precomputed_ranges=None) → single [history_start, target_end] span
```

After:
```
run_minute_refetch → build_symbol_minute_coverage(conn, symbol) → _do_minute_symbol(
    coverage_index=<{symbol: days}>, force_reset_terminal=True, via="refetch")
  → _needs_seed=True (force_reset_terminal still forces re-seed)
  → precomputed_ranges = compute_missing_minute_sessions(...) → coverage-aware ranges
  → update_data_gaps(precomputed_ranges=<ranges>, force_reset_terminal=True)
       → step 2 resets terminal rows in window; step 4 uses precomputed_ranges
```

`run_minute_cycle`'s data flow is unchanged (`via="cycle"` added to its
existing coverage_index build/pass-through).

### State Management

No new persistent state. `coverage_index` remains a per-invocation,
in-memory `dict[str, set[date]]` built fresh each call. For
`run_minute_refetch` the dict contains exactly one symbol's covered days
(a few thousand entries), built by a `WHERE symbol = %s` query — the
universe-wide scan (18.46s / 22.7M pairs at the 2026-07-28 audit, and a
multi-GB Python dict) is never paid by the single-symbol operator command.
`run_minute_cycle` continues to build the universe index once per cycle,
where the cost is amortized across ~11.6k symbols.

## Technical Decisions

### Patterns and Conventions

- `via` is a plain `str` parameter with two call-site literals (`"refetch"`,
  `"cycle"`), not a project-wide enum — it is a log-field discriminator
  local to two callers in one module pair, not a value compared in
  conditionals or dispatched on. This does not conflict with the
  project's "no magic strings" rule, which targets logic dispatch;
  nothing branches on `via`.
- `force_reset_terminal` and coverage-aware seeding are now independent
  axes, matching how `update_data_gaps` already models them internally
  (`force_reset_terminal` at step 2, `precomputed_ranges` at step 4 — two
  separate steps, already orthogonal in the implementation the callers
  just weren't exercising correctly).

### Amendment (2026-07-28): per-symbol coverage query for refetch

The original design reused the universe-wide `build_minute_coverage_index`
in `run_minute_refetch`, justified by slice 162's measured ~3s scan cost as
"acceptable interactive latency." During Phase 6 verification that premise
was found stale: the scan now runs 18.46s / 22.7M (symbol, day) pairs —
9.4× output growth from continuous backfill since 162, not a regression —
and exceeded the 30s `MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT` when run
while the daemon was mid-cycle, which forced `run_minute_refetch` into the
legacy single-span fallback: the exact behavior this slice exists to
eliminate, merely logged instead of silent. Full audit:
`user/reference/prod-scale-and-coverage-scan-baseline.md`.

Resolution: `run_minute_refetch` builds a **single-symbol** coverage index
(`build_symbol_minute_coverage`, `WHERE symbol = %s` against the same 4h
cagg). Slice 162 measured the per-symbol shape at ~2.0s and rejected it
solely because 2s × 12k symbols was untenable *for the daemon loop*; that
rejection never applied to a one-symbol operator command, and post-163
rechunk (119 well-sized chunks vs ~140 tiny ones per scan then) it is
cheaper still. Unification is preserved semantically: both paths seed via
`compute_missing_minute_sessions` over the same cagg-derived day-grain
coverage — they differ only in how much of the universe they index, which
matches their scopes (12k-symbol cycle vs 1-symbol refetch).

Companion change: `MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT` 30s → 90s for
the daemon's universe scan (scope item 6). The longer-term day-grain
coverage cagg is filed as GitHub issue #3 — explicitly out of scope here.

### Audit Findings (scope item 2)

Swept `cli/commands/data.py`, `data/acquisition/daemon/{daily,minute,runner}.py`,
and `data/gaps/` for other pairs of user-facing entry points whose labels
imply equivalent behavior but diverge:

1. **`run_daily_refetch` vs. `run_daily_cycle`** — differ only in
   `force_reset_terminal` (refetch: `True`) and window resolution
   defaults (refetch: `first_data_date`→`last_completed_session`; cycle:
   `DAILY_HISTORY_FLOOR`→`last_completed_session`). Both always call
   `update_data_gaps` with `precomputed_ranges=None`, which for
   `granularity == "daily"` is irrelevant — daily's step 4 always calls
   `compute_missing_ranges` regardless of `precomputed_ranges` (see
   `update_data_gaps.py:166-181`). **Not the same defect class as minute**
   — no silent full-window fallback exists for daily because there is no
   coverage-index short-circuit to bypass. Resolved: no code change beyond
   the `via` log marker (observability parity, not a fix for a real bug).
2. **`mt data pull` vs. `mt data get`** — `pull` fetches from the
   provider and writes; `get` reads already-stored bars. Names and help
   text already make the distinction unambiguous (verb "pull" vs. "get");
   no overlap in accepted arguments implying equivalence. Not an instance
   of the pattern.
3. **`mt data caggs refresh` vs. the automatic refresh policies** — refresh
   policies run on a schedule; the CLI command triggers an immediate
   one-off refresh. Distinct mechanisms, but both are internally
   documented (163/168 design docs) and neither claims to be "the same
   as" the other from the CLI help text. Not an instance of the pattern
   in the sense this slice cares about (a label silently selecting
   between algorithms with the same apparent inputs/outputs).
4. **`daemon run --symbols` vs. bare `daemon run`** — same code path
   (`run_minute_cycle`/`run_daily_cycle`), scoped by symbol list; not a
   divergent implementation, just a smaller input. Not an instance.

No further instances found requiring resolution. This finding — including
the daily "looks the same, isn't dangerous" case — is the deliverable for
scope item 2 and is recorded here as the permanent record (the plan entry
will reference this section rather than duplicating it).

## Implementation Details

### Migration Plan

This is a refactor of existing runtime code paths, not a data migration.

- **Source:** `run_minute_refetch` seeds via `precomputed_ranges=None`
  (legacy single-span fallback in `update_data_gaps`).
- **Destination:** `run_minute_refetch` seeds via `precomputed_ranges`
  computed from a freshly-built single-symbol coverage index
  (`build_symbol_minute_coverage`) — same day-grain cagg source and same
  `compute_missing_minute_sessions` diff as `run_minute_cycle`.
- **Consumers to update:** none outside `minute.py` — `run_minute_refetch`'s
  signature (`symbol`, `from_date`, `to_date`) is unchanged; its only
  caller (`_pull_fetch_inner` in `cli/commands/data.py`) requires no edit.
- **Behavior verification:** existing `test/unit/data/acquisition/daemon/test_minute.py`
  T9 tests (`TestRunMinuteRefetch`) currently assert `run_minute_refetch`
  passes `coverage_index=None` implicitly (by not asserting otherwise) and
  `force_reset_terminal=True` explicitly (`test_force_reset_terminal_always_true`).
  These tests are updated to assert a coverage index is built and passed
  through, mirroring the equivalent `run_minute_cycle` assertions already
  present in the T7/T8 test classes. `force_reset_terminal=True` assertion
  is retained unchanged — that behavior does not change.
- The window-clamping behavior in `run_minute_refetch` (`from_date`/`to_date`
  resolution) is unchanged — coverage-aware seeding operates *within*
  whatever window is resolved, same as it does for the daemon path's
  `[history_start, target_end]`.

## Integration Points

### Provides to Other Slices

None — this slice does not add new interfaces. It changes the internal
behavior of an existing operator command to match its sibling.

### Consumes from Other Slices

- Slice 162's `compute_missing_minute_sessions` — consumed identically to
  how `run_minute_cycle` already consumes it. The new
  `build_symbol_minute_coverage` (this slice) is a per-symbol sibling of
  162's `build_minute_coverage_index` and adopts its contract verbatim —
  same staleness guard, same timeout, same fail-safe described below.
  `build_minute_coverage_index` fails safe by design (slice 162/168): a
  `statement_timeout`, other operational/connection error mid-scan, or a
  stale source cagg (slice 168's `assert_cagg_fresh` guard) are all caught
  *inside* `build_minute_coverage_index` and logged at ERROR there — none
  of them propagate as an exception. The function returns `None` in every
  one of those cases, never raises. `run_minute_refetch` does not need to
  (and does not) enumerate these failure modes itself, for the same reason
  `run_minute_cycle` doesn't today: the contract is "returns `None` on any
  operational failure," not "raises specific exceptions the caller must
  catch." On `None`, `run_minute_refetch` falls back to the same legacy
  single-span behavior `run_minute_cycle` falls back to today — existing,
  intentional fail-safe behavior (slice 162), not new behavior introduced
  by this slice. No new failure surface is introduced: `run_minute_refetch`
  already opens `pool.connection()` blocks for other purposes earlier in
  the function (e.g. `_resolve_minute_history_start`), under the same pool
  and the same `PoolTimeout`/`LockNotAvailable` handling
  `_process_minute_symbol` already has.

## Success Criteria

### Functional Requirements
- `mt data pull 1m --symbol X` against a symbol with a partially-covered
  history seeds only the genuinely-missing sessions in the requested
  window, not a full `[history_start, target_end]` span — verified by
  gap-row count and span comparison before/after, mirroring slice 162's
  own verification pattern.
- `force_reset_terminal` behavior (clearing `PROVIDER_HOLE`/`RETRY_EXHAUSTED`
  rows in scope) is unchanged for `run_minute_refetch`.
- Every `INFO`/`WARNING`/`ERROR` log line from `_do_minute_symbol`,
  `_process_minute_symbol`, `_do_daily_symbol`, and `_process_daily_symbol`
  includes a `via=refetch` or `via=cycle` field.
- Slice 162's Verification Walkthrough is corrected to the settled
  `mt data daemon run --minute --symbols ...` form with a cross-reference
  to this slice; the inline "found during Phase 6" correction note is
  replaced (the caveat about routing through a different code path no
  longer applies).
- `user/reference/minute-fetch-code-paths.md` is marked superseded
  (frontmatter `status: superseded`, note pointing to this slice) — not
  deleted, since it remains useful as the historical record of the defect.

### Technical Requirements
- No new modules; changes confined to `minute.py`, `daily.py` (log marker
  only), `minute_coverage.py` (new `build_symbol_minute_coverage`
  function), `constants.py` (timeout value only), and the two doc updates.
- `ruff` and `mypy`/`pyright` clean on touched files, at or below `main`
  baseline.
- Existing unit test suites for `minute.py`/`daily.py` daemon paths pass;
  `TestRunMinuteRefetch` tests updated per Migration Plan above; no new
  test file needed — extend existing test classes.

### Verification Walkthrough

1. **Confirm the defect no longer reproduces** — pick a symbol with a
   known partial history gap (or seed one on `trading_test`). Before
   this slice's fix, `mt data pull 1m --symbol X` would seed a single
   `[history_start, target_end]` gap row. After:
   ```bash
   uv run mt data pull 1m --symbol X -v
   ```
   Inspect `data_gaps` for that symbol/window — expect gap rows matching
   only the genuinely-missing sessions, same shape as what
   `mt data daemon run --minute --symbols X -v` would produce for the
   same starting state.

2. **Confirm `force_reset_terminal` still works** — seed a `RETRY_EXHAUSTED`
   row for a test symbol, then run `mt data pull 1m --symbol X`. Confirm
   the row is reset and re-attempted (existing behavior, unchanged).

3. **Confirm log markers** — run both paths with `-v` and grep the output
   (or `MT_LOG_FORMAT=json` output) for `via=`:
   ```bash
   uv run mt data pull 1m --symbol X -v 2>&1 | grep via=refetch
   uv run mt data daemon run --minute --symbols X -v 2>&1 | grep via=cycle
   ```
   Both should match.

4. **Confirm docs updated** — `user/slices/162-slice.coverage-aware-minute-gap-seeding.md`'s
   Verification Walkthrough uses the corrected command with no caveat
   about divergent code paths; `user/reference/minute-fetch-code-paths.md`
   frontmatter shows `status: superseded`.

5. **Run unit tests:**
   ```bash
   uv run pytest test/unit/data/acquisition/daemon/test_minute.py test/unit/data/acquisition/daemon/test_daily.py -q
   ```
   Expected: all green, including updated `TestRunMinuteRefetch` assertions.

## Implementation Notes

### Development Approach

1. Add `via` parameter threading first (mechanical, no behavior change,
   easy to verify in isolation).
2. Unify `run_minute_refetch`'s coverage-index build (the actual fix).
3. Update the two docs (162 walkthrough, reference doc supersession note).
4. Update/extend unit tests last, once the implementation is settled.

### Special Considerations

- `run_minute_refetch`'s coverage build adds one per-symbol grouped scan
  (`build_symbol_minute_coverage`, expected low single-digit seconds; slice
  162 measured ~2.0s pre-163-rechunk) to every single-symbol `pull 1m`
  invocation — acceptable interactive latency with no universe-scan
  timeout exposure. No caching/sharing across invocations is introduced —
  each `pull 1m` call is independent, matching current semantics.
- The daemon's universe scan is unchanged in shape but its timeout rises
  30s → 90s (scope item 6) so the converging backfill growth cannot push
  the once-per-cycle build into permanent fail-safe fallback.
