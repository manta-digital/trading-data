---
docType: review
layer: project
reviewType: code
slice: minute-acquisition-correctness
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/921-slice.minute-acquisition-correctness.md
aiModel: z-ai/glm-5.3
status: complete
dateCreated: 20260910
dateUpdated: 20260910
reviewedSha: 9b54f27bdc6b62bca9890bc5006bbac73f2ea6bc
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 35
findings:
  - id: F001
    severity: concern
    category: logging
    summary: "\"trailing phase complete\" is logged even when the trailing phase aborted or stopped early"
    location: "src/manta_trading/data/acquisition/daemon/minute.py#run_minute_cycle"
  - id: F002
    severity: concern
    category: logging
    summary: "The cutover log file — the auditable report — omits the acceptance numbers"
    location: "scripts/cutover_921_minute_sessions.py#_repair"
  - id: F003
    severity: concern
    category: design
    summary: "Cutover stop-trigger is coupled to a literal journal format string with no verified drift guard"
    location: "scripts/cutover_921_minute_sessions.py"
  - id: F004
    severity: concern
    category: error-handling
    summary: "A crashed minute pass still exits 0 — the new exit-code signal misses the crash path"
    location: "src/manta_trading/data/acquisition/daemon/runner.py#_record_minute_pass_outcome"
  - id: F005
    severity: note
    category: documentation
    summary: "data_health docstring still names the removed EODHD quota check"
    location: "src/manta_trading/cli/commands/health.py#data_health"
  - id: F006
    severity: note
    category: documentation
    summary: "mt-minute-pass.timer comment no longer explains the 04:05 firing"
    location: "deploy/systemd/mt-minute-pass.timer:4-6"
  - id: F007
    severity: note
    category: design
    summary: "Cutover loop edge semantics worth recording as deliberate"
    location: "scripts/cutover_921_minute_sessions.py#main"
  - id: F008
    severity: note
    category: error-handling
    summary: "Preflight provider failures surface as raw tracebacks"
    location: "scripts/cutover_921_minute_sessions.py#_remaining_credits"
  - id: F009
    severity: note
    category: structure
    summary: "minute.py is well past the ~300-line guideline"
    location: "src/manta_trading/data/acquisition/daemon/minute.py"
  - id: F010
    severity: pass
    category: conventions
    summary: "Firing times have one source, mechanically enforced in both directions"
    location: "src/manta_trading/constants.py"
  - id: F011
    severity: pass
    category: error-handling
    summary: "Failure-kind threading and the no-answer accounting gate are well designed"
    location: "src/manta_trading/data/acquisition/outcomes.py"
  - id: F012
    severity: pass
    category: correctness
    summary: "Session-aware chunk judgment fixes the spillover-bar bug at the right granularity"
    location: "src/manta_trading/data/acquisition/daemon/minute_sessions.py"
  - id: F013
    severity: pass
    category: data-safety
    summary: "The repair writes through the single writer and verifies with the health check's own code"
    location: "scripts/repair_921_minute_sessions.py"
---

# Review: code — slice 921

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.3

## Findings

### [CONCERN] "trailing phase complete" is logged even when the trailing phase aborted or stopped early

`_logger.info("trailing phase complete: %d symbols", trailing_scanned)` runs unconditionally after `_run_minute_phase` returns, but the phase returns COMPLETE, QUOTA_EXHAUSTED, PROVIDER_UNAVAILABLE, or `None` (shutdown). In the three non-complete cases the journal asserts a completion that did not happen: a quota abort at 500 of 13,083 symbols logs both `minute pass aborted: quota_exhausted in the trailing phase — 500 symbols scanned, 12583 not attempted` and `trailing phase complete: 500 symbols`. The companion report fields do it right (`minute_pass_outcome`, `minute_trailing_completed`), and `state.py`'s `MinutePassOutcome` docstring explicitly says outcomes must be comparable values, not reconstructed from log text — this line is a log-text assertion of an outcome the value path says is false. Note also that `pass_outcome = trailing_outcome or MinutePassOutcome.COMPLETE` labels an operator-stopped pass COMPLETE in the report (exit 0 is right; the label is not). The line should carry the phase outcome, or only say "complete" when `trailing_outcome is MinutePassOutcome.COMPLETE`.

### [CONCERN] The cutover log file — the auditable report — omits the acceptance numbers

`_repair` does `print(output)` to the terminal, but `start_log`'s tee only captures `say` lines, so `921-cutover-*.log` records "running scripts/repair_921_minute_sessions.py --verify" and "every acceptance bar met after N firing(s)" while none of the before/after images, FAIL lines, or measured counts reach the file. `cutover_common.start_log`'s own docstring states the motivation: "The first 921 cutover's only record was terminal scrollback across two ten-hour firings. The narration is the cutover's report; it gets a file" — and the script docstring promises "every action is logged — this runs with root privileges and must be auditable afterwards." The content the log was built to preserve is exactly the content not in it. Route the repair output through the log (append `output` to the log file alongside the terminal print, or `say` the report text).

### [CONCERN] Cutover stop-trigger is coupled to a literal journal format string with no verified drift guard

`TRAILING_COMPLETE = re.compile(r"trailing phase complete: (\d+) symbols")` must match `minute.py`'s format string `"trailing phase complete: %d symbols"` forever. CLAUDE.md: "NEVER use user-accessible labels as logical structure. They are fragile." If the log line is ever reworded, `fire_unit_until` never sees the pattern and `wait_for_unit_or_line` falls back to waiting for the unit to END — which, for a backfill-bound minute firing, is days (the exact hazard the helper's docstring describes). The abort side is more robust (`(\S+)` captures any outcome token, and the abort line is rendered from the enum members), but the completion line is a bare literal duplicated across two files with no shared constant. I could not verify whether `test/unit/test_cutover_921.py` pins the regex to `minute.py`'s emitter; if it does not, add a guard test asserting the pattern against the source format string, or move the line's text into a constant both files import.

### [CONCERN] A crashed minute pass still exits 0 — the new exit-code signal misses the crash path

`minute.py`'s contract is that non-429 4xx "propagates and crashes the cycle"; `_loop` catches it (`minute_report = None`), stamps the cycle end, and `_record_minute_pass_outcome(None)` returns without touching `_minute_exit_code`. With `--stop-when-done` (the unit's ExecStart), the next iteration finds the minute gate closed, is not awaiting a first cycle, and exits with code 0 — so `mt-minute-pass.service` reports success for a pass that crashed without collecting the session. That is precisely the outcome the Task 4.6 exit mapping exists to surface; the None-report behavior is documented ("leaves the code unchanged") but leaves the worst pass outcome on the old silent path. Consider mapping a None minute report to `MINUTE_EXIT_PASS_INCOMPLETE`: a crashed pass certainly did not collect the current session.

### [NOTE] data_health docstring still names the removed EODHD quota check

The CLI-visible docstring reads "Check data freshness, cagg lag, EODHD quota, and Kalshi phase recency" — the quota check was removed (Decision 6) and the module docstring was updated accordingly, but the command help still advertises it.

### [NOTE] mt-minute-pass.timer comment no longer explains the 04:05 firing

"Staggered 30 minutes behind mt-daily-pass.timer" is true only of the 13:05 firing (12:35 + 30m); 04:05 exists for EODHD publication timing per the `MINUTE_PASS_FIRING_TIMES_UTC` docstring. The stale comment invites an operator to "restore" 01:05 — the drift-guard test would catch it, but the file should state the real reason for the time it now carries.

### [NOTE] Cutover loop edge semantics worth recording as deliberate

Three asymmetries: (a) `MAX_MINUTE_FIRINGS`' comment says reaching the ceiling "is reported as a FAIL", but `ok` derives only from the final verify text and `aborted` — a ceiling run whose acceptance verify comes back clean reports CUTOVER COMPLETE; (b) a post-trailing `QUOTA_EXHAUSTED` — which the daemon maps to exit 0 as "the designed steady state" — marks the cutover INCOMPLETE because `PASS_ABORTED`'s `(\S+)` discards the phase; the docstring says this is deliberate ("reported, never retried"), but daemon and cutover disagree on severity for the same event; (c) a firing that crashes (no trailing line, no abort line — `_completed` False, `aborted` None) does not break the loop; the narration records `unit result=failed exit=…` but only verify text stops iteration. All are bounded and reported; recording so the behavior is chosen rather than accidental.

### [NOTE] Preflight provider failures surface as raw tracebacks

`__main__` handles only `CutoverError`; an httpx connection error, a non-200 from EODHD, or a missing `dailyRateLimit` key raises and prints a stack trace instead of the script's characteristic "cutover refused: …" operator UX. Not silent — just unpolished for a script whose preflight is otherwise entirely check-then-act with explained refusals.

### [NOTE] minute.py is well past the ~300-line guideline

~1,100 lines after this slice added ~270. The slice did extract `minute_sessions.py` (daemon) and `minute_session_mass.py` (CLI) to respect the guideline, but `_advance_minute_gap`, `_bar_to_row`/`_insert_minute_bars`, and the exit-code mapping block are further extraction candidates ("where practical").

### [PASS] Firing times have one source, mechanically enforced in both directions

`MINUTE_PASS_FIRING_TIMES_UTC`, `mt-minute-pass.timer`, and the Kalshi timer's collision comment were all updated together, and `test/unit/deploy/test_units.py::TestMinutePassTimerMatchesTheConstant` asserts constant→timer, timer→constant, and the count. This is exactly the "one value, one source" convention CLAUDE.md requires, enforced by a test rather than prose — and it is the reason the 01:05→04:05 change is safe.

### [PASS] Failure-kind threading and the no-answer accounting gate are well designed

`response_carries_an_answer` as a sidecar keeps `classify_outcome`'s shared daily contract untouched; `MinuteFailureKind` rides both the normal return path and the except handlers so the provider-failure breaker never trips on DATABASE faults (with an inline justification on each handler, satisfying the exception-handling rule); the quota path breaks before tallying while counting the attempted symbol in its log line, matching its comment; and the exit mapping is exhaustive via the module-level assert, with the QUOTA_EXHAUSTED-after-trailing exception documented where it is decided.

### [PASS] Session-aware chunk judgment fixes the spillover-bar bug at the right granularity

The `(open, close]` predicate is the same one `repair_921.py`'s truncation index applies to stored bars, so the daemon and the repair agree on what a truncated session is; `bisect` over sorted stamps is clean; sessions closing after `chunk_end` are excluded from judgment; chunks with no judgeable session keep the classifier's verdict; and the `day_end_utc` widening is documented as a fetch-layer mapping only, with gap accounting deliberately kept on the un-extended bound.

### [PASS] The repair writes through the single writer and verifies with the health check's own code

No `data_gaps` SQL of its own — `update_data_gaps` under the daemon's advisory lock with `LockNotAvailable` handled per symbol and skipped-and-reported; `run_verify` imports `select_judged_session`/`fetch_session_mass`/`check_minute_session_mass` rather than re-implementing the measurement; straddling rows widen the window so the containment delete reaches them; the stale-cagg `[WAIT]` path refuses to judge rather than reporting a stale mass; and the truncation-scan timeout raises rather than returning an empty index (never report an unmeasured universe as clean).
