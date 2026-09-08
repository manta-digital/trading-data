---
docType: review
layer: project
reviewType: slice
slice: minute-acquisition-correctness
project: trading-data
verdict: FAIL
sourceDocument: project-documents/user/slices/921-slice.minute-acquisition-correctness.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260908
dateUpdated: 20260908
reviewedSha: 1eab485f66fe4a21cbc4993224165b038470c4d8
findings:
  - id: F001
    severity: fail
    category: architecture-boundary
    summary: "Repair script writes `data_gaps` without naming the single-writer/locking path"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:108-118"
  - id: F002
    severity: concern
    category: contract-change
    summary: "Minute range end deviates from the architecture's documented range normalization with no escalation"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:192-197"
  - id: F003
    severity: concern
    category: hidden-dependency
    summary: "Consumers of minute `gap_end` are not analyzed; `coalesce_data_gaps` adjacency is a concrete break"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:166-168"
  - id: F004
    severity: concern
    category: under-specification
    summary: "`minute session mass` is under-specified and its baseline is poisoned by the defect window"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:127-133"
  - id: F005
    severity: concern
    category: error-handling
    summary: "Only HTTP 402 has a stated failure policy in the new two-phase cycle"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:120-126"
  - id: F006
    severity: note
    category: scope
    summary: "Quota prioritization is intra-pass only"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:180-187"
  - id: F007
    severity: note
    category: no-magic-strings
    summary: "`quota_exhausted` should be a named constant, not a literal"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:186"
  - id: F008
    severity: pass
    category: alignment
    summary: "Maintenance-band scope is correct and corrective"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md#technical-scope"
  - id: F009
    severity: pass
    category: alignment
    summary: "Dependency direction and verification surface"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:139-152"
---

# Review: slice — slice 921

**Verdict:** FAIL
**Model:** claude-opus-5

## Findings

### [FAIL] Repair script writes `data_gaps` without naming the single-writer/locking path

Scope item 2 has `scripts/repair_921_minute_sessions.py` reset ~24k terminal rows to UNKNOWN and seed new UNKNOWN rows with corrected ends, and Decision 6 (line 210) justifies it as a script rather than a migration — but nowhere does the design state *how* those rows are written. 900-arch's maintenance-band constraints name slice 145's `update_data_gaps`-as-single-writer rule as the canonical contract a maintenance slice consumes rather than redefines; 140-arch (`#### update_data_gaps … — transactional writer`) specifies a single transaction under a PostgreSQL advisory lock on `(symbol, granularity)`, with `force_reset_terminal=True` as the *published* mechanism for clearing `PROVIDER_HOLE`/`RETRY_EXHAUSTED` (the path `mt data refetch` already uses). A bespoke script issuing direct `UPDATE`/`INSERT` against `data_gaps` bypasses both the writer and the lock. The cutover (line 256) runs `--apply` just after 00:00 UTC, ~35 minutes before the 00:35 daily and 01:05 minute firings — an overrun puts the repair's writes in a race with a daemon holding the advisory lock for the same scope, corrupting the exact accounting this slice exists to fix. The design must state either "the repair calls `update_data_gaps(..., force_reset_terminal=True)` per symbol scope" or, if it cannot, why a direct writer is safe and how it serializes against the daemon.

### [CONCERN] Minute range end deviates from the architecture's documented range normalization with no escalation

140-arch specifies that missing-session ranges are "normalized as `[session_open_utc, session_close_utc]` per session" (step 6: `(symbol, granularity, session_open_utc(T_first), session_close_utc(T_last))`) for both the daily and minute data tables. Technical Decision 1 adopts a third semantics — next UTC midnight — and states it as "not `session_close_utc`", which is a knowing deviation from the owning initiative's specified contract, yet the doc never cites that specification or records an escalation. 900-arch is explicit: "A fix that requires changing a contract is escalated to the owning initiative rather than absorbed here." The extended-hours rationale is convincing; what is missing is the acknowledgement that this changes a 140-owned contract and the resulting split — daily rows ending at session close, minute rows at midnight — for a shared table.

### [CONCERN] Consumers of minute `gap_end` are not analyzed; `coalesce_data_gaps` adjacency is a concrete break

The Component Structure table changes `compute_missing_minute_sessions`'s output shape and lists only the seeder, the daemon, the selector, health, and the scripts. 140-arch defines `coalesce_data_gaps` adjacency as `next_trading_session_after(A.gap_end) == B.gap_start` with `B.gap_start` a `session_open_utc` — a `gap_end` at UTC midnight is not a session boundary, so adjacent minute rows will either stop coalescing (unbounded row growth in `data_gaps`) or coalesce incorrectly. The doc does not mention `coalesce_data_gaps` at all, nor the serving-side consumers that report gap ranges to API clients (slices 184/185/187), whose reported minute coverage windows shift by up to ten hours. At minimum the design needs a line per consumer stating "unchanged because …" or "adjusted by …".

### [CONCERN] `minute session mass` is under-specified and its baseline is poisoned by the defect window

Three constants are named (`HEALTH_MINUTE_MASS_BASELINE_SESSIONS`, `HEALTH_MINUTE_MASS_MIN_RATIO`, `MINUTE_TRAILING_PRIORITY_WINDOW`) with no values, unlike sibling slice 919, which states every threshold inline (4 d / 5 d / 20k / 3 h) and justifies them as "the loosest values that catch each failure within a working day". Two consequences follow from the omission. First, the baseline is "the median of the previous N sessions" — at cutover every one of those sessions holds ~45k bars against a healthy ~2.0M, so the new check certifies a 2%-collection production as healthy until the window rolls past 2026-08-31; Success Criterion 6 ("passes on production after the repair") is satisfied trivially by a poisoned baseline. Second, the check runs at :50 hourly against "the last completed session", but that session's bars are only fetched by the following night's pass — without a stated collection-lag tolerance the check fails for most of the hours of every day by construction, which is precisely the "a unit that is always failed is not an alarm" pathology this slice removes the quota check for (line 82).

### [CONCERN] Only HTTP 402 has a stated failure policy in the new two-phase cycle

Scope item 3 and Decision 4 fully specify the 402 path (abort, `quota_exhausted`, no gap row touched, no `attempt_count` increment). The other failure modes on the same new I/O path are unstated: request timeout/hang, connection reset mid-response, HTTP 429, and provider 5xx storms. The measured pathology being fixed — "still ran 35 minutes, skipping one symbol at a time … 7,755 rows promoted to `RETRY_EXHAUSTED` without a provider answer" — is not unique to 402; a 5xx storm or a timeout cascade reproduces it exactly, and the trailing phase now guarantees every active symbol is touched every pass, widening the blast radius. Similarly, the repair script's failure modes are unenumerated: what state exists if `--apply` dies partway (the "refuses to run twice" idempotency claim at line 117 assumes a completed apply), and whether the reset+seed is one transaction or many.

### [NOTE] Quota prioritization is intra-pass only

The trailing/backfill ordering is defined inside the minute cycle, but the 100k/day allowance is shared with the 00:35 daily pass, which runs first, and with the two later minute firings. The design's own measurement (line 74) attributes starvation to spend ordering *across* firings. The trailing phase is small enough (~13k one-chunk requests) that this is probably adequate, but the doc should say so rather than leave the cross-pass ordering unaddressed.

### [NOTE] `quota_exhausted` should be a named constant, not a literal

900-arch's "No magic strings" principle requires dispatch and status values to be enums or typed constants defined once. The new pass outcome appears only as a quoted string in the Data Flow and in Success Criterion 4; the constants row (line 170) lists the three thresholds but not the outcome value. Worth one word in the design given the health/journal/`mt-run status` surfaces that will match on it.

### [PASS] Maintenance-band scope is correct and corrective

All five in-scope items repair behavior that is already specified and demonstrably wrong (truncated fetches, retries consumed without a provider answer, a health check measuring the wrong quantity, a UTC-labelled local timestamp), which is exactly the "corrective, not additive" test 900-arch sets for the 900-999 band. Touching acquisition and data-quality modules owned by initiatives 120/140 is explicitly permitted by the 2026-08-03 scope extension. The Out of Scope list correctly pushes delisting-aware status (#14), mat-chunk recompression, and the Kalshi `partial` exit elsewhere.

### [PASS] Dependency direction and verification surface

`dependencies: [919]` with `interfaces: [162, 165, 912]` has the maintenance slice depending on the work it corrects, which 900-arch states is the expected direction for this band (the "900 precedes 100-180" statement covers foundation slices only). The health check is added into 919's existing `gather()`/`render()` structure rather than as a parallel mechanism, and every outcome is reachable through `mt` (`mt data health`, `--check`, `mt-run status`), satisfying "CLI is the verification surface". Thresholds land in `constants.py` per the centralized-configuration goal, and Decision 5 removes a check rather than tuning it to a value that would mask the condition — consistent with "Explicit failure".
