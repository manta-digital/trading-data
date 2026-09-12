---
docType: review
layer: project
reviewType: code
slice: operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/922-slice.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md
aiModel: z-ai/glm-5.3
status: complete
resolution: addressed
resolvedSha: d42c9c8
resolvedDate: 20260912
dateCreated: 20260912
dateUpdated: 20260912
reviewedSha: 9b237599c04a2a78de127263ee935a9248ce63a2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 23
findings:
  - id: F001
    severity: concern
    category: security
    summary: "EODHD API key can leak into the overview screen and logs via the httpx error message"
    location: "src/manta_trading/api/eodhd_account.py#fetch_credit_usage"
  - id: F002
    severity: concern
    category: correctness
    summary: "`mt data status` default summary misreports a healthy database and skips the promised screen"
    location: "src/manta_trading/cli/commands/data.py#data_status"
  - id: F003
    severity: concern
    category: async-correctness
    summary: "Synchronous pass-run recording inside `async def run_pass` blocks the event loop"
    location: "src/manta_trading/cli/commands/kalshi.py#run_pass"
  - id: F004
    severity: concern
    category: correctness
    summary: "`daily_pass_completed` appears to be set before the walk finishes, defeating the COMPLETE/INCOMPLETE distinction"
    location: "src/manta_trading/data/acquisition/daemon/daily.py:472"
  - id: F005
    severity: concern
    category: error-handling
    summary: "Accounting pass is unbounded at two layers, and DB errors other than OperationalError break its documented exit contract"
    location: "src/manta_trading/cli/commands/accounting.py#data_accounting"
  - id: F006
    severity: concern
    category: robustness
    summary: "Same-pid open rows are never swept, so a swallowed close failure orphans a RUNNING row for the process's lifetime"
    location: "src/manta_trading/data/acquisition/daemon/pass_run_recorder.py#PassRunRecorder._close_abandoned"
  - id: F007
    severity: note
    category: design
    summary: "`render_universe` parses the recorded human-readable summary by string prefix"
    location: "src/manta_trading/cli/rendering/overview.py#render_universe"
  - id: F008
    severity: note
    category: structure
    summary: "Render layer imports from the command layer"
    location: "src/manta_trading/cli/rendering/overview.py"
  - id: F009
    severity: note
    category: error-handling
    summary: "Bare `except Exception` without a BLE noqa, and a pool that is never closed"
    location: "src/manta_trading/cli/commands/_pass_run.py#make_pass_run_recorder"
  - id: F010
    severity: note
    category: design
    summary: "Minute walk anchor is derived from the calendar day rather than from what the cycle actually did"
    location: "src/manta_trading/data/acquisition/daemon/runner.py#Runner._open_minute_run"
  - id: F011
    severity: pass
    category: correctness
    summary: "Recording contract, exhaustiveness guards, and degraded-mode handling are consistently well done"
    location: "src/manta_trading/data/acquisition/daemon/pass_run_recorder.py"
---

# Review: code — slice 922

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.3

## Findings

### [CONCERN] EODHD API key can leak into the overview screen and logs via the httpx error message

`fetch_credit_usage` builds the request URL with the token inlined (`url = f"{EODHD_USER_ENDPOINT}?api_token={api_key}&fmt=json"`) and then calls `response.raise_for_status()`. `httpx.HTTPStatusError` messages embed the full request URL ("Client error '403 Forbidden' for url '...api_token=<KEY>&fmt=json'"), so on any non-2xx answer the raw key travels inside the exception. `overview.gather` (src/manta_trading/cli/commands/overview.py, the `except Exception` block) then does `facts.credits_error = credits_unavailable(str(exc))` and `_logger.warning("overview: credit lookup failed: %s", exc)` — so the key is printed on the overview screen (plain-text and `--json` `credits_text`) and written to the journal. This matters doubly because the renderer's own docstring says this screen is designed to be "paste[d] into an issue". The `ValueError` path correctly uses `redact_token(url)`, which shows the intent, but the `raise_for_status()` path bypasses it. Note that switching to `params={"api_token": ...}` does not by itself fix the `HTTPStatusError` message (httpx still renders the merged URL); the guard needs to be at the `gather`/catch boundary (e.g., catch `httpx.HTTPStatusError` and report only the status code, or redact `str(exc)` before display). Also, the f-string URL is inconsistent with the rest of the codebase (`cutover_921_minute_sessions.py`, `lists_refresh_sp500`), which passes the token via `params=`.

### [CONCERN] `mt data status` default summary misreports a healthy database and skips the promised screen

The new default path filters rows to `GAPS,STALE,FAILED` (`_DEFAULT_HEALTH_FILTER`), so on a database where every `(symbol, granularity)` row is OK, `status_rows` is empty. The pre-existing empty check `if not status_rows and symbol is None:` then prints "No instruments found. Run `mt data instruments rebuild` to populate the registry." and exits 0 — before `render_status_sources`/`render_status_footer` are ever reached. That message is factually wrong in this case (the registry is fully populated and healthy), and it defeats the slice's stated purpose: the docstring promises "With no options this prints the source freshness block and the health footer", but the happy path — the primary case the feature was built for — renders neither. This check predates the slice, but the slice redefined what the default invocation means and must reconcile the two (e.g., gate the empty-registry message on an unfiltered count, or on the health-count query, rather than on the filtered rows). Relatedly, the summary path still executes `fetch_status_rows_with_freshness` with the non-OK filter and then discards the rows entirely — potentially materializing thousands of 11-column rows just to test emptiness that `fetch_all_health_counts_with_freshness` already answers. Skipping the row fetch when `not wants_table` would make the summary both correct and cheap.

### [CONCERN] Synchronous pass-run recording inside `async def run_pass` blocks the event loop

`run_pass` is `async def`, but `recorder.open(...)`, `recorder.progress(...)` (via `_phase_reporter`, fired at each phase start from inside the async `CollectionPass` loop), and `recorder.close(...)` are all synchronous psycopg calls. Worse, `_recorder_over_connect` opens a **fresh connection per write** with `connect_timeout=PASS_RUN_DB_CONNECT_TIMEOUT_SECONDS` (5 s), and `open()` additionally performs the abandoned-run sweep (a SELECT plus possible UPDATEs) before the INSERT — several round trips. The project rule is explicit: synchronous code inside an `async def` must run in <1 ms worst case; anything longer must go through `run_in_executor`/thread. In the worst case (unreachable or slow DB) each recording call blocks the loop for up to ~5+ seconds, stalling the Kalshi client's rate-limit scheduling and every other coroutine on the loop. The existing `sink.emit` precedent is at least documented as "exactly twice, outside any phase's I/O"; the recorder here is called up to six times, including per phase. Wrapping the recorder calls in `asyncio.to_thread` (or making the recorder loop-safe) would satisfy the rule.

### [CONCERN] `daily_pass_completed` appears to be set before the walk finishes, defeating the COMPLETE/INCOMPLETE distinction

Caveat on evidence: I could not open the full body of `run_daily_cycle` beyond the diff, so the surrounding control flow is unverified — please confirm. From the diff's indentation and position, the added `report.daily_pass_completed = True` sits inside a per-symbol-scope `try:` whose `except QuotaWaitAborted:` logs "quota wait aborted by shutdown — exiting", i.e., before or during the symbol walk rather than after it. The loop-exhaustion `else:` clause added at daily.py:511-517 is the correct mechanism ("No break: every pending symbol was attempted"). If the earlier assignment executes on any symbol whose quota wait succeeds (or once before the walk starts), then a later early exit — a `should_continue` break between symbols, or a later `QuotaWaitAborted` — leaves the flag `True`, and `_close_daily_run` records `COMPLETE` for an interrupted pass. That is precisely the case the field's own docstring says must be `INCOMPLETE` ("False means the pass stopped early — a shutdown between symbols, or an aborted quota wait"), and it would put a wrong verdict on the overview screen this slice exists to make trustworthy. If the assignment is redundant, delete it and keep only the `else:` clause; if it is meant to mark something else, the intent is not recoverable from the code.

### [CONCERN] Accounting pass is unbounded at two layers, and DB errors other than OperationalError break its documented exit contract

The command's own docstring and the service unit say "exits 2 only when it could not run at all", but the `except` catches only `psycopg.OperationalError`. `health.py` deliberately also catches `psycopg.errors.QueryCanceled` (its comment explains why: a statement timeout means "could not run"); accounting performs a full-universe aggregate documented as "several minutes against the production database" with no `statement_timeout` on its connection, and the new unit sets `TimeoutStartSec=infinity`. So a stuck or timed-out accounting scan (a) exits with a traceback and a non-2 code, contradicting the stated contract, and (b) leaves its `pass_runs` row open — it will later be swept as "abandoned: pid gone", which reports a failure with no cause, exactly what the Kalshi code's comment says to avoid. Given this project's own incident history with unbounded statements (the 2026-07-20 discipline: bound the statement, cancel the backend on timeout), a daily full-universe scan that is unbounded at both the statement layer and the unit layer deserves at least a generous finite `TimeoutStartSec` and a `QueryCanceled` → exit-2 mapping consistent with `health`.

### [CONCERN] Same-pid open rows are never swept, so a swallowed close failure orphans a RUNNING row for the process's lifetime

The abandoned-run sweep excludes rows with `run.pid == self._pid`. That protects a concurrent same-pid pass, but the daemon opens a **new** minute/daily row every cycle, and `PassRunRecorder.close` deliberately swallows `psycopg.Error` after logging (the recorder-never-raises contract). So the anticipated failure mode — DB dies mid-pass, `open` succeeded, `close` failed — leaves an open row carrying the daemon's own pid that this process can never sweep. Every subsequent cycle opens another row, and `mt data overview` will show two (then three...) "RUNNING" minute passes indefinitely, which is the exact state the renderer docstring says "an operator needs shown, not summarised" — except here it is a phantom. Consider sweeping same-pid rows whose `run_id` differs from the just-opened one and whose `started_at` predates this recorder's construction time, which is safe without pid-liveness guessing.

### [NOTE] `render_universe` parses the recorded human-readable summary by string prefix

The overview strips a leading `"minute universe: "` from the accounting pass's recorded `pass_runs.detail` before re-labeling it. This uses a user-visible label as logical structure — the project's rules name that exact anti-pattern as fragile: rewording `summary_line` in `minute_accounting` silently double-prints the label on the overview with no test failing. Either record a machine-usable payload (or the bare numbers) in the accounting detail, or assert the prefix contract in a shared constant/test shared by both modules, as was done for `MINUTE_TRAILING_COMPLETE_LINE`.

### [NOTE] Render layer imports from the command layer

`cli/rendering/overview.py` imports constants (`NEVER_RUN`, `NO_ACCOUNTING`) and dataclasses (`Overview`, `PassLine`, `SourceFreshness`, ...) from `cli/commands/overview.py`, inverting the usual direction (commands import rendering, as `data_status` does). It works today only because `commands/overview.py` defers its own rendering import inside `data_overview`'s body, so there is no import-time cycle — but the cycle is one moved import away. The shared dataclasses/constants would sit more naturally in a leaf module (e.g., a `cli/rendering/overview_types.py` or a shared models module) that both layers can depend on.

### [NOTE] Bare `except Exception` without a BLE noqa, and a pool that is never closed

Two small items in one function: (1) `except Exception:` with `_logger.exception` and a `return None` is a justified, documented swallow — but the project's required ruff config selects `BLE`, and the sibling catch in `overview.gather` carries `# noqa: BLE001` while this one does not, so lint will flag it (add the noqa with the same justification comment). (2) The `ConnectionPool` built for the daemon is never `close()`d — there is no teardown in `daemon_run` or `Runner`. For the `--forever` production shape process exit handles it, but an explicit `--stop-while-done` all-active run also constructs the pool and relies entirely on interpreter-exit behavior for the pool's background workers; a `close()` on the exit path would make that deterministic. (Also trivial: the module docstring says the pool would block "for its own thirty-second timeout", but the configured `PASS_RUN_DB_CONNECT_TIMEOUT_SECONDS` is 5 — the comment has drifted from the constant.)

### [NOTE] Minute walk anchor is derived from the calendar day rather than from what the cycle actually did

`_open_minute_run` sets `walk_anchor_at` whenever `is_firing_day(now.date(), ...)` is true — a per-day property evaluated before the cycle runs. The design intent (per the pass_runs docstring and this method's own comment) is that the anchor marks a full universe walk; if a firing-day process runs multiple minute cycles and any later cycle is backfill-only, that cycle still resets every symbol's staleness clock. I could not verify the trailing-phase gating inside `run_minute_cycle` from the material available, so this may be unreachable in the production unit shape (`--stop-when-done`, one cycle per firing) — but the invariant is currently approximated by the calendar rather than enforced by the cycle, and the cycle's own `minute_trailing_required` on the report is the authoritative signal. Worth a comment confirming the invariant, or a test that a firing-day backfill-only cycle does not anchor.

### [PASS] Recording contract, exhaustiveness guards, and degraded-mode handling are consistently well done

The slice's core discipline holds up well across every file I read: every recorder call site tolerates `None` recorder and `None` run_id; the recorder catches only `psycopg.Error` and logs with `_logger.exception` (no bare `except:`, no `except Exception: pass`); the `EXIT_BY_OUTCOME` / `PASS_RUN_OUTCOME_BY_SYNC_OUTCOME` / `_MINUTE_PASS_RUN_OUTCOME` / `_OUTCOME_TEXT` dicts are each guarded by an exhaustive `assert` against their enum so a new member cannot silently exit 0 or record COMPLETE; `health.verdict_line` is shared between the rendered output and the recorded detail so the two cannot drift; `overview._read`/`_newest` roll back the poisoned transaction before continuing so one missing table cannot lose the whole screen; the credit call is bounded by `EODHD_ACCOUNT_TIMEOUT_SECONDS` and degrades to a line rather than a failure; and the DRY goals are genuinely met (`_pass_run.py` centralizes recorder construction, `read_source_freshness`/`render_source_sources` are shared between `overview` and `status`, `firing_schedule.schedule_for` is the single cadence source). The renamed `EODHD_USER_ENDPOINT` and `firing_schedule` leave no dangling references, and the timer-drift constants are documented against their systemd units.

---

## Resolution

Addressed in `8cdff9f` (F001–F004) and `d42c9c8` (F005–F010), on branch
`922-slice.operator-overview-...`. Unit tier: 3427 passed, 0 failed. Every
fix carries a regression test, and each test was checked against the
pre-fix code to confirm it actually fails there.

| id | disposition | note |
|---|---|---|
| F001 | fixed | Redacted at the raise site in `fetch_credit_usage` *and* at the `gather` catch. The test fixture now reproduces httpx's real message (`Client error '403' for url '...api_token=...'`); the old one said only `"401"`, which is why the leak survived review. |
| F002 | fixed | Empty-registry is now decided by the unfiltered health counts, not by rows the non-OK filter returned. The summary path no longer fetches rows at all — correct and cheaper. Two senses of "empty" are both covered: no rows in the view, and `{"OK": 0}`. |
| F003 | fixed | `on_phase` is now awaitable and every recorder call goes through `asyncio.to_thread`. A test asserts the write lands on a thread other than the loop's. |
| F004 | **premise wrong, concern real** | The assignment is *after* `_run_steady_state_cycle` returns, not before the walk — the reviewer flagged its own evidence gap here. But the helper returns early on a shutdown check and on a failed bulk call, and breaks mid-loop on shutdown, and the caller set the flag on any normal return. Completion now lives inside the helper at the point the walk actually ends, mirroring the BACKFILL `for/else`. |
| F005 | fixed (in part) | `QueryCanceled` now maps to exit 2 and closes the row, matching `health`. `TimeoutStartSec=infinity` is **kept**: it is a deliberate, documented choice on the unit (a timeout would turn a slow night into a failed unit), and the row-closing fix removes the consequence the finding was actually about. |
| F006 | fixed | The recorder sweeps its own pre-existing rows, scoped to before the recorder was constructed. Folded into the existing `open_runs` read rather than added as a third query — the first attempt cost an extra connection per `open()` and surfaced as cross-test stdout pollution. |
| F007 | fixed | `MINUTE_UNIVERSE_LABEL` is shared by writer and reader. The test feeds the real `summary_line` into the renderer and fails if the two desync. |
| F008 | fixed | Shapes moved to `cli/overview_types.py`, a leaf module. A test asserts importing the renderer does not load the command module. |
| F009 | fixed | All three parts: the blind-except carries its suppression, `make_pass_run_recorder` returns the pool so the daemon closes it, and the docstring no longer names a thirty-second timeout that matched no constant. |
| F010 | fixed | The calendar decides the anchor at `open` (all `open` can know); `close` withdraws it when the cycle reports no trailing work. Reachable in production: `--stop-when-done` runs several cycles per firing. Architecture doc amended. |
