---
docType: tasks
slice: 148-slice.mt-data-refetch
project: trading
lld: user/slices/148-slice.mt-data-refetch.md
dependencies:
  - 145-slice (update_data_gaps, coalesce_data_gaps, daemon daily/minute per-symbol functions)
  - 146-slice (runner, daemon cycle entry points)
  - 147-slice (mt data status — upstream operator surface)
projectState: |
  Slice 147 merged to main. The following are in place:
  - data_gaps table with FetchStatus enum (UNKNOWN, PROVIDER_HOLE, FAILED_RETRYABLE, RETRY_EXHAUSTED)
  - update_data_gaps(force_reset_terminal=False) in src/manta_trading/data/gaps/update_data_gaps.py
  - coalesce_data_gaps in src/manta_trading/data/gaps/coalesce_data_gaps.py
  - _do_daily_symbol in src/manta_trading/data/acquisition/daemon/daily.py
  - _do_minute_symbol in src/manta_trading/data/acquisition/daemon/minute.py
  - mt data status command in src/manta_trading/cli/commands/data.py
  - Advisory lock held by _do_*_symbol for full per-symbol operation
dateCreated: 20260504
dateUpdated: 20260504
status: complete
---

# Tasks: 148 — mt data refetch

## Context Summary

Slice 148 adds `mt data refetch`, an operator escape valve that re-fetches a symbol's
data window from the provider using the existing daemon fetch path, with
`force_reset_terminal=True` so terminal gap rows (`PROVIDER_HOLE`, `RETRY_EXHAUSTED`)
are reset to `UNKNOWN, attempt_count=0` before re-attempting.

**Implementation strategy:** extend `_do_daily_symbol` and `_do_minute_symbol` with
`force_reset_terminal` and `window` parameters; add `run_daily_refetch` /
`run_minute_refetch` entry points; add `mt data refetch` Typer command.

No new advisory-lock code. No new fetch logic. `coalesce_data_gaps` runs after all
chunks for both granularities.

---

## Tasks

- [x] **T1: Create slice branch**
  - [x] Verify current branch is `main`: `git branch --show-current`
  - [x] Create and switch to branch: `git checkout -b 148-slice.mt-data-refetch`
  - [x] Success: `git branch --show-current` returns `148-slice.mt-data-refetch`

- [x] **T2: Extend `_do_daily_symbol` — add `force_reset_terminal` and `window` params**
  - [x] In `src/manta_trading/data/acquisition/daemon/daily.py`, add optional parameters
        to `_do_daily_symbol`:
        - `force_reset_terminal: bool = False`
        - `window: tuple[date, date] | None = None`
  - [x] When `window` is provided, constrain the EODHD fetch range to `[window[0], window[1]]`
        instead of the full `[first_data_date, last_completed_session]` default
  - [x] Pass `force_reset_terminal` through to `update_data_gaps` call
  - [x] Do NOT add `coalesce_data_gaps` inside `_do_daily_symbol` — coalescing the daily
        path is not part of the normal daemon cycle and must not be added there.
        `coalesce_data_gaps(symbol, 'daily')` is called by `run_daily_refetch` after
        `_do_daily_symbol` returns (T4), keeping the normal daemon cycle unchanged.
  - [x] Default behavior (`force_reset_terminal=False`, `window=None`) must be unchanged
  - [x] Success: existing unit/integration tests for `_do_daily_symbol` still pass

- [x] **T3: Unit tests for `_do_daily_symbol` extensions**
  - [x] Test: `force_reset_terminal=True` is forwarded to `update_data_gaps` call
  - [x] Test: `window=(date1, date2)` constrains the fetch date range
  - [x] Test: `window=None` uses the full default range (no regression)
  - [x] Test: `force_reset_terminal=False` (default) is forwarded correctly (no regression)
  - [x] Test: `coalesce_data_gaps` is NOT called inside `_do_daily_symbol`
  - [x] Run: `python -m pytest test/unit/data/acquisition/daemon/test_daily.py -v`
  - [x] Success: all tests pass, no existing tests broken

- [x] **T4: Add `run_daily_refetch` entry point**
  - [x] In `src/manta_trading/data/acquisition/daemon/daily.py`, add:
        ```python
        def run_daily_refetch(
            symbol: str,
            *,
            from_date: date | None = None,
            to_date: date | None = None,
        ) -> CycleReport:
        ```
  - [x] Resolves `from_date` → `instruments.first_data_date` if `None`
  - [x] Resolves `to_date` → last completed trading session if `None`
  - [x] Calls `_do_daily_symbol(symbol, window=(from_date, to_date), force_reset_terminal=True)`
  - [x] Calls `coalesce_data_gaps(symbol, 'daily')` after `_do_daily_symbol` returns
  - [x] Returns the resulting `CycleReport`
  - [x] Success: function is importable, type-checks, does not alter `run_daily_cycle`

- [x] **T5: Unit tests for `run_daily_refetch`**
  - [x] Test: `from_date=None` resolves to `first_data_date` from instruments
  - [x] Test: `to_date=None` resolves to last completed session
  - [x] Test: explicit `from_date`/`to_date` passed through to `_do_daily_symbol` window
  - [x] Test: `force_reset_terminal=True` is always set (never False)
  - [x] Run: `python -m pytest test/unit/data/acquisition/daemon/test_daily.py -v`
  - [x] Success: all tests pass

- [x] **T6: Extend `_do_minute_symbol` — add `force_reset_terminal` and `window` params**
  - [x] In `src/manta_trading/data/acquisition/daemon/minute.py`, add optional parameters
        to `_do_minute_symbol`:
        - `force_reset_terminal: bool = False`
        - `window: tuple[date, date] | None = None`
  - [x] When `window` is provided, clamp `from_date` to
        `max(first_data_date, today - MINUTE_HISTORY_MONTHS)` and `to_date` to last
        completed trading session close UTC (same clamping as normal cycle, just windowed)
  - [x] Pass `force_reset_terminal` through to every `update_data_gaps` call in the
        chunk loop
  - [x] Confirm `coalesce_data_gaps(symbol, 'minute')` is already called at the end of
        `_do_minute_symbol`'s chunk loop (existing behavior from slice 145). If it is
        present, no change needed. If absent, add it.
  - [x] Default behavior (`force_reset_terminal=False`, `window=None`) must be unchanged
  - [x] Success: existing unit/integration tests for `_do_minute_symbol` still pass

- [x] **T7: Unit tests for `_do_minute_symbol` extensions**
  - [x] Test: `force_reset_terminal=True` forwarded to each `update_data_gaps` call in
        chunk loop
  - [x] Test: `window=(date1, date2)` constrains chunk iteration range
  - [x] Test: `window=None` uses default range (no regression)
  - [x] Test: `force_reset_terminal=False` default unchanged
  - [x] Test: `coalesce_data_gaps` is called after the chunk loop (both default and
        refetch paths)
  - [x] Run: `python -m pytest test/unit/data/acquisition/daemon/test_minute.py -v`
  - [x] Success: all tests pass, no existing tests broken

- [x] **T8: Add `run_minute_refetch` entry point**
  - [x] In `src/manta_trading/data/acquisition/daemon/minute.py`, add:
        ```python
        def run_minute_refetch(
            symbol: str,
            *,
            from_date: date | None = None,
            to_date: date | None = None,
        ) -> CycleReport:
        ```
  - [x] Resolves and clamps window per slice design §`run_minute_refetch` step 1
  - [x] Calls `_do_minute_symbol(symbol, window=(...), force_reset_terminal=True)`
  - [x] Returns `CycleReport`
  - [x] Success: function importable, type-checks, does not alter `run_minute_cycle`

- [x] **T9: Unit tests for `run_minute_refetch`**
  - [x] Test: `from_date=None` clamps to `max(first_data_date, today - MINUTE_HISTORY_MONTHS)`
  - [x] Test: `to_date=None` resolves to last completed session
  - [x] Test: explicit window passed through to `_do_minute_symbol`
  - [x] Test: `force_reset_terminal=True` always set
  - [x] Run: `python -m pytest test/unit/data/acquisition/daemon/test_minute.py -v`
  - [x] Success: all tests pass

- [x] **T10: Commit daemon extensions**
  - [x] `git add src/manta_trading/data/acquisition/daemon/daily.py`
  - [x] `git add src/manta_trading/data/acquisition/daemon/minute.py`
  - [x] `git add test/unit/data/acquisition/daemon/`
  - [x] `git commit -m "feat(148): extend _do_daily/minute_symbol with force_reset_terminal + window; add run_*_refetch"`
  - [x] Success: commit created, test suite green

- [x] **T11: Add `mt data refetch` Typer command**
  - [x] In `src/manta_trading/cli/commands/data.py`, add
        `@data_app.command("refetch")` with flags:
        - `--symbol` (required TEXT)
        - `--daily` (bool flag, default False)
        - `--minute` (bool flag, default False)
        - `--from` / `from_date` (optional DATE string, YYYY-MM-DD)
        - `--to` / `to_date` (optional DATE string, YYYY-MM-DD)
        - `--dry-run` (bool flag, default False)
        - `--yes` / `-y` (bool flag, default False)
        - `--json` / `as_json` (bool flag, default False)
  - [x] Flag resolution: `--daily` and `--minute` both False → resolve to both; one
        set → that granularity only; both set → both
  - [x] Validate `--symbol` in instrument registry; exit non-zero with clear message if not found
  - [x] Validate `--from` ≤ `--to` if both provided
  - [x] Dry-run path: SELECT terminal gaps in scope from `data_gaps`, print preview
        table, exit 0 — no provider calls, no DB writes
  - [x] Normal path: print preview table of terminal gaps, prompt unless `--yes`/`--json`,
        call `run_daily_refetch` and/or `run_minute_refetch`, print `CycleReport` summary
  - [x] No-terminal-gaps case: print notice, prompt "Refetch anyway? [y/N]" unless
        `--yes`/`--json`
  - [x] JSON mode: skip prompt, emit JSON per slice design §JSON mode output
  - [x] Success: `mt data refetch --help` shows all flags; command is reachable

- [x] **T12: Integration tests for `mt data refetch` CLI**
  - [x] File: `test/integration/test_data_refetch.py`
  - [x] Test: `--symbol NOTREAL` exits non-zero with symbol-not-found message
  - [x] Test: `--dry-run` with seeded terminal gap — gap visible in output, no DB mutation
  - [x] Test: `--yes` with seeded terminal gap — fetch runs, gap resolved, no prompt
  - [x] Test: no terminal gaps in scope + `--yes` — refetch proceeds (SC12: no-terminal-gaps
        path completes successfully when confirmed)
  - [x] Test: `--daily` alone — only daily refetch called; `--minute` alone — only minute
  - [x] Test: neither `--daily` nor `--minute` — both granularities refetched
  - [x] Test: `--json` output is valid JSON with expected keys; prompt skipped
  - [x] Test: `--from`/`--to` constrain window (verify via mock or DB inspection)
  - [x] Note: advisory lock contention (SC8) is verified manually in T14 Step 5; no
        automated concurrent-process test is included (two-process coordination is
        fragile in CI)
  - [x] Skip tests without DB (`pytest.mark.integration`)
  - [x] Run: `python -m pytest test/integration/test_data_refetch.py -v`
  - [x] Success: all non-skipped tests pass

- [x] **T13: Commit CLI command and integration tests**
  - [x] `git add src/manta_trading/cli/commands/data.py`
  - [x] `git add test/integration/test_data_refetch.py`
  - [x] `git commit -m "feat(148): add mt data refetch Typer command"`
  - [x] Success: commit created, full unit suite green (`pytest test/unit/ -q`)

- [x] **T14: Verification walkthrough**
  - [x] Follow all steps in slice design §Verification Walkthrough
  - [x] Step 1: seed terminal gap, confirm visible in `mt data status --symbol SPY --health failed`
  - [x] Step 2: `mt data refetch --symbol SPY --daily --from 2023-11-01 --to 2023-11-30 --dry-run` —
        preview shows gap, SQL confirms no mutation
  - [x] Step 3: run with confirmation — fetch executes, summary printed
  - [x] Step 4: SQL + `mt data status` confirm gap resolved
  - [x] Step 5: concurrent lock test — second process blocks on same symbol
  - [x] Step 6: unknown symbol exits non-zero
  - [x] Cleanup: delete synthetic test gap
  - [x] Success: all walkthrough steps produce expected output

- [x] **T15: Documentation and status updates**
  - [x] Update `CHANGELOG.md` — add entry for slice 148
  - [x] Update `DEVLOG.md` — add entry for slice 148
  - [x] Mark slice 148 complete in `user/architecture/140-slices.data-quality-operations.md`
        (change `[ ]` to `[x]` on entry 8)
  - [x] Update slice design frontmatter: `status: complete`
  - [x] Update this task file frontmatter: `status: complete`
  - [x] `git add` all documentation files
  - [x] `git commit -m "docs(148): mark slice complete; update CHANGELOG, DEVLOG"`
  - [x] Success: `git log --oneline -5` shows documentation commit
