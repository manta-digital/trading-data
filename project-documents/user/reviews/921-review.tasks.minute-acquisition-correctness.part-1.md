---
docType: review
layer: project
reviewType: tasks
slice: minute-acquisition-correctness
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/921-tasks.minute-acquisition-correctness-1.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260909
dateUpdated: 20260909
reviewedSha: 0110652f6cedbde18062c2518ad6f56ebfe12fd1
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "Breaker's primary trigger (5xx/429) does not flow through the exception path Task 4.2 fixes"
    location: "src/manta_trading/data/acquisition/daemon/minute.py:456"
  - id: F002
    severity: concern
    category: scope-boundary
    summary: "Task 2.3 changes a shared classifier's contract without stating the daily-path obligation"
    location: "src/manta_trading/data/acquisition/outcomes.py#classify_outcome"
  - id: F003
    severity: note
    category: test-coverage
    summary: "SC6's journal-line assertion is implemented but not tested"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:389-391"
  - id: F004
    severity: note
    category: clarity
    summary: "Task 4.1's grep success criterion is self-contradictory as written"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-1.md:340-341"
  - id: F005
    severity: note
    category: clarity
    summary: "Task 4.6's `--forever` decision is lower-stakes than the task implies"
    location: "deploy/systemd/mt-minute-pass.service:16"
  - id: F006
    severity: note
    category: test-sequencing
    summary: "Section 4 stacks three implementation tasks before its first test task"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-1.md:343-410"
  - id: F007
    severity: pass
    category: completeness
    summary: "Success-criteria coverage and file split are complete"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-1.md"
  - id: F008
    severity: pass
    category: test-coverage
    summary: "Load-tier NFR and CI gating are handled explicitly, not implicitly"
    location: ".github/workflows/ci.yml:3"
---

# Review: tasks — slice 921

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Findings

### [CONCERN] Breaker's primary trigger (5xx/429) does not flow through the exception path Task 4.2 fixes

Task 4.2 scopes the "failure kind" work to `_process_minute_symbol`'s five `except` handlers (minute.py:249–281). Task 4.4 then counts consecutive *provider failure* kinds listing "5xx, timeout, connection reset, 429 exhaustion", and Task 4.5's headline case is "five consecutive 5xx → `PROVIDER_UNAVAILABLE`".

But 5xx and 429 never raise. `classify_outcome` **returns** `TRANSIENT_FAILURE` for them (verified at outcomes.py:72), so at minute.py:456 the chunk loop receives a value and `_do_minute_symbol` returns normally — through the `try`, not through any handler. Only timeout and connection reset (`httpx.HTTPError`/`TimeoutException`) actually reach an `except`. Task 2.3 creates the transport-vs-classified distinction as a value inside the chunk loop, but no task states that this value must be threaded up through `_do_minute_symbol`'s return → `_process_minute_symbol`'s return → `run_minute_cycle`.

Failure scenario: a junior AI implements Task 4.2 literally — extends the five `except` handlers with a failure-kind field — and Task 4.5's five-consecutive-5xx test fails because the 5xx path returns the same kindless `TRANSIENT_FAILURE` it does today. SC5's "five consecutive symbol failures end it with `PROVIDER_UNAVAILABLE`" then holds only for timeouts, which is the narrower half of the failure modes the design names. Task 4.2's second bullet should name `_do_minute_symbol`'s normal return alongside the handlers, and its success criterion (currently only "a `PoolTimeout` and an `httpx.ReadTimeout` are distinguishable") should add a returned-5xx case.

### [CONCERN] Task 2.3 changes a shared classifier's contract without stating the daily-path obligation

Task 2.3 requires "Distinguish the two kinds of `TRANSIENT_FAILURE` **at the point of classification** … Carry the distinction as a value." The point of classification is `classify_outcome`, which is shared: minute.py:456 and **daily.py:737** both call it.

Every other shared-surface task in this file explicitly fences the collateral path — Task 1.1 requires `fetch_sessions`'s body be "byte-identical", Task 2.2 says "if the daily path depends on the increment, gate the new behavior on granularity explicitly", Task 3.2 says "existing callers pass nothing and behave identically". Task 2.3 alone is silent about daily.

Failure scenario: the implementer widens `classify_outcome`'s return type (e.g. to a tuple or a richer enum) to carry the kind; daily.py:737 unpacks a `LastAttemptOutcome` and either breaks at runtime or is edited into an unreviewed behavior change in the daily acquisition path, which this slice's Out of scope explicitly leaves alone. Add the fence: state whether the distinction is a new return value, a sidecar, or a new member, and that daily.py:737's behavior must not move.

### [NOTE] SC6's journal-line assertion is implemented but not tested

SC6 has two halves: the phase order **and** "the journal shows `trailing phase complete: N symbols` before the first backfill line". Task 3.4 emits the line; Task 3.5's three tests assert request ordering, one-chunk-per-symbol, and no double-fetch — none captures the log. Since the line is the operator's only in-production evidence that the trailing phase ran to completion (it is what the design's Verification Walkthrough greps for), a caplog assertion in Task 3.5 is cheap and closes SC6 fully.

### [NOTE] Task 4.1's grep success criterion is self-contradictory as written

"`grep` for the literal strings outside `state.py` returns only the enum definition" — the enum definition *is* in `state.py`, so a search that excludes `state.py` cannot return it. The intent (no bare `"QUOTA_EXHAUSTED"` / `"PROVIDER_UNAVAILABLE"` / `"COMPLETE"` string literals anywhere but the enum) is clear from Decision 7, but the criterion as phrased cannot be executed as stated. Reword to "grep for the literal strings returns matches only in `state.py`'s enum definition."

### [NOTE] Task 4.6's `--forever` decision is lower-stakes than the task implies

Task 4.6 asks the implementer to "State what a `--forever` runner that ran several minute cycles exits with: the last cycle's outcome, or the worst seen. Pick one" — with no analysis offered, unlike Task 3.3, which supplies both options and their measured costs. Worth stating in the task that `ExecStart` runs `mt data daemon run --minute --stop-when-done`, so `--forever` is a hand-run/dev path only and neither choice affects the unit's exit code in production. Without that, a junior AI may treat an inconsequential decision as a blocking one.

### [NOTE] Section 4 stacks three implementation tasks before its first test task

The Review Response claims F010 ("tests batched") is addressed by testing "after the abort/breaker (Task 4.5) and again after the exit mapping (Task 4.7)". That is an improvement over one terminal test task, but 4.2 (return-signature change across every caller), 4.3 (402 abort), and 4.4 (breaker) still land before 4.5. Task 4.2 in particular changes `_process_minute_symbol`'s signature and requires updating every existing caller and test — a natural place to stop and re-green before layering the abort on top. Sections 1–3 do not have this shape; only Section 4 does.

### [PASS] Success-criteria coverage and file split are complete

SC1 → Tasks 1.1–1.5 (both halves: the close-ended range *and* the built URL's `to`, with the summer/winter fixture SC1 demands and an explicit "assert against the fixture's close, not an offset from the open"). SC4 → Tasks 2.1–2.4, covering both accounting paths, with 2.1 correctly written as a characterization test against unmodified code. SC5 → Tasks 4.1, 4.3–4.7. SC6 → Tasks 3.4–3.5. SC2/SC3/SC7/SC8 are in file 2 (verified: Sections 5–7 cover the health check, repair script, cutover, and issue closeout), so nothing is orphaned by the split. I found no task that fails to trace to a Scope item or success criterion — including Tasks 3.3 and 4.2, which are new since the last review and trace to protecting the quota (Scope 3) and to making the breaker implementable (SC5).

### [PASS] Load-tier NFR and CI gating are handled explicitly, not implicitly

Scope 4 restates a read-bound NFR (`HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT = 30s`, sub-second cagg read). File 2's Task 5.8 adds `test/load/test_921_minute_session_mass_nfr.py` over the existing `prod_shaped_db` fixture, alongside the established `test_167_*` / `test_169_*` NFR tests. CI gating is not left implicit: the task states outright that `ci.yml` is publish-on-tag only and requires a documented manual invocation in the test docstring and runbook 100, deferring the CI job to slice 907. I verified `ci.yml` — it has no test job, so the claim is accurate rather than an excuse.
