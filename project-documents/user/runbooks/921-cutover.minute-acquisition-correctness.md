---
docType: cutover
project: trading-data
slice: minute-acquisition-correctness
scope: production
host: manta9000
dateCreated: 20260910
dateUpdated: 20260911
status: complete
lld: user/slices/921-slice.minute-acquisition-correctness.md
issues: [19, 20, 22]
---

# 921 cutover — minute acquisition correctness

Post-merge operations for slice 921. These steps were Tasks 7.7–7.10 of the
slice; they can only run after the branch is merged and tagged on `main`, so
they left the task file to let the review gate run before anything is
published. The cutover script is `scripts/cutover_921_minute_sessions.py`;
its design and the 2026-09-10 findings are in the slice doc and issue #22.

Prerequisite: the code review on `921-slice.minute-acquisition-correctness`
has passed the gate and the branch is merged.

- [x] **1. Release and cutover**
  - [x] Branch merged to `main` after the code review; `v0.14.1` tagged on
        `main` and pushed.
  - [x] No pass active (`systemctl is-active mt-minute-pass.service
        mt-daily-pass.service` both inactive; stop them on manta9000 if not).
  - [x] `uv run python scripts/cutover_921_minute_sessions.py --ref v0.14.1`
        from the `main` checkout. It applies the repair (which resets the
        4,278 one-bar 2026-09-09 sessions), fires the daily pass, fires the
        minute pass and stops it at `trailing phase complete`, waits for the
        cagg refresh, and writes `--verify` to
        `project-documents/user/notes/921-cutover-<stamp>.log`. It refuses
        below 100,000 remaining credits.
  - [x] Success: the report shows the repair applied, both passes fired, and
        the `--verify` measurements.
- [x] **2. Confirm the acceptance criteria from the cutover report**
  - [x] **Know which judged session is being read.** The judged session is
        the newest one whose 04:05 UTC firing plus the 3 h lag has passed —
        07:05 UTC the next day. A cutover run before 07:05 UTC judges D-2.
        Read the mass figure for the session the fired pass actually
        collected (the log names it), and state that in the report.
  - [x] From the Task 7.7 report, confirm: truncated symbol-days over the last
        five NYSE sessions = 0; bars for the judged session ≥ 1,000,000 (SC3);
        `OK minute session mass` with the measured line; the journal shows
        `trailing phase complete: N symbols` before any backfill line (SC6).
  - [x] The one genuinely time-bound observation — at least one `healthy`
        production run after 23:00 UTC (SC7) — is recorded as a follow-up
        note against the issue, not as a task blocking the slice. Make it an
        explicit named artifact: a comment on #19 stating the outstanding SC7
        clause and the check-back instruction, so the slice cannot close with
        it silently lost.
  - [x] If any criterion misses, record the measurement and stop — do not
        apply a speculative fix without the actual evidence.
  - [x] Success: every measurement recorded in the slice notes.
- [x] **3. Close issues #19, #20 and #22**
  - [x] Close #19 with the root cause (session-open range end), the fix, and
        the before/after measurements from Tasks 6.8 and 7.8.
  - [x] Close #20 with the cagg-freshness verification already measured
        (every cagg fresh, migrations 053/054 applied), noting the optional
        recompression as deferred (design Out of scope).
  - [x] Close #22 with the `--verify` lines from the 0.14.1 cutover log and
        the count of 2026-09-09 one-bar sessions after `--apply`.
  - [x] Success: both issues closed with measurements, not assertions (SC8).
- [x] **4. Record**
  - [x] Commit the cutover log and this checklist:
        `docs: record the 921 cutover measurements and close #19/#20/#22`.


## Outcome (2026-09-11)

Executed by Claude, 2026-09-10/11, under the sudoers rule in
`deploy/sudoers.d/manta-ops`. Deviations from the plan above:

- The cutover ran three times (v0.14.1 by the PM, v0.14.3 and v0.14.5 by
  Claude) as each run exposed a further defect; all are in issue #22 and
  fixed in 0.14.2–0.14.6. Production is on 0.14.9.
- Measured acceptance (08:00 UTC 2026-09-11): judged session 2026-09-09
  1,549,744 bars; 6,868 symbols ≥30 bars; health line PASS (3,974
  bars/min); `trailing phase complete: 13083 symbols` on every firing.
- SC3 (truncated symbol-days = 0 over the last five sessions) is NOT met at
  closeout: 10,738 remain and drain with backfill on the daily allowance;
  extras are spent. #22 stays open for that measurement. #19 and #20 closed
  with measurements.
- `MT_MINUTE_FIRING_DAYS=Sat` was set in production on 2026-09-11 (weekly
  current-session collection, daily allowance to backfill); the judged
  session and SC3 must be read against that policy from here on.
- Cutover narration logs: `project-documents/user/notes/921-cutover-*.log`
  (ignored by git; kept on the host).
