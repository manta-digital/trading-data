---
docType: review
layer: project
reviewType: slice
slice: minute-acquisition-correctness
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/921-slice.minute-acquisition-correctness.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260908
dateUpdated: 20260908
reviewedSha: b3ab98950c5f19eceead171dbcdc584a2e5cc146
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "`minute session mass` floors and collection lag are derived from a full 20:00 UTC close and misfire on early-close sessions"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:171-189"
  - id: F002
    severity: concern
    category: under-specification
    summary: "The mass check's measurement source and read cost are unspecified"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:180-186"
  - id: F003
    severity: concern
    category: operator-surface
    summary: "Exit 0 for a backfill-phase `PROVIDER_UNAVAILABLE` abort contradicts the project's exit-3 partial convention and hides a provider outage"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:159-165"
  - id: F004
    severity: concern
    category: integration
    summary: "Interaction with slice 912's cycle-end stamping on an aborted pass is unstated"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:262-266"
  - id: F005
    severity: note
    category: scope
    summary: "Two documented deviations from the 900 slice-plan entry"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:106-116"
  - id: F006
    severity: note
    category: architectural-boundary
    summary: "Signature extensions to 140-owned functions are not escalated the way the daily-path deviation is"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:227-230"
  - id: F007
    severity: pass
    category: alignment
    summary: "Dependency direction and maintenance-band placement are correct"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:204-219"
  - id: F008
    severity: pass
    category: error-handling
    summary: "Failure modes for the new I/O paths are enumerated with explicit handling, not TBD"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:142-165"
  - id: F009
    severity: pass
    category: correctness
    summary: "Every reader of the changed value is analyzed before the change"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:238-252"
  - id: F010
    severity: pass
    category: alignment
    summary: "No magic strings; new dispatch values are typed and centralized"
    location: "project-documents/user/slices/921-slice.minute-acquisition-correctness.md:233"
---

# Review: slice — slice 921

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Findings

### [CONCERN] `minute session mass` floors and collection lag are derived from a full 20:00 UTC close and misfire on early-close sessions

Both halves of the new check are anchored to a regular session. The `8 h` lag is justified as "the 01:05 UTC firing plus its run" — which only holds for a 20:00 UTC close (→ judged from 04:00 UTC). On an NYSE early close (13:00 ET → 17:00 UTC in summer, 18:00 in winter), `session_close_utc + 8 h` puts the judged session in scope from 01:00/02:00 UTC, i.e. at or before the 01:05 firing has finished collecting it. The threshold has the same shape: a 210-minute early close is ~54% of a 390-minute session, so the healthy 1.9–2.0M mass becomes ~1.05M against a `HEALTH_MINUTE_SESSION_MIN_BARS = 1,000,000` floor — inside noise of the floor, and below it once the reduced liquidity of a half-day (Black Friday, July 3, Christmas Eve) is included. Decision 5 (line 290-293) argues the floors "survive an illiquid Friday" but never mentions early closes, even though slice 144 materializes `session_close_utc` with early-close overrides precisely so consumers can see them. Failure scenario: on the Friday after Thanksgiving the pass works correctly, the judged session holds ~950k bars, `mt data health` fails `minute session mass`, `mt-health.timer` fails hourly, and the operator learns to ignore the unit — the exact "a unit that is always failed is not an alarm" failure this slice exists to remove (line 89-90).

### [CONCERN] The mass check's measurement source and read cost are unspecified

Scope item 4 states the two measured quantities ("bars in the judged session", "symbols with `≥ 30` bars") and their floors, but never names the relation they are read from. The Component Structure table (line 232) lists only `cli/commands/health.py` as changed, and Interfaces Required (line 217) names `minute_4hour_ohlcv` only as the *coverage* source for the seeder. This matters because the 140 architecture records a measured latency cliff on exactly this table family — a single-symbol `MIN(time)/MAX(time)` on raw `minute_ohlcv` took 10m47s (140-slices §166), and the `data_status` bars summary had to be moved off the raw hypertable onto a coverage cagg to meet its NFR (§167). Failure scenario: the implementer reads the count from raw `minute_ohlcv` with a `time BETWEEN open AND close` predicate across ~13k symbols; the hourly `mt-health.timer` firing takes minutes or trips a statement timeout, and the check exits 2 (919's "could not run"), failing the unit for a reason unrelated to data mass. State the source relation (e.g. summing `minute_count` from `minute_4hour_ohlcv` over the session's buckets) and the read bound.

### [CONCERN] Exit 0 for a backfill-phase `PROVIDER_UNAVAILABLE` abort contradicts the project's exit-3 partial convention and hides a provider outage

The exit mapping is "0 when the trailing phase completed … 3 when the abort fell inside the trailing phase". This folds two very different outcomes into exit 0: `QUOTA_EXHAUSTED` during backfill (genuinely the designed steady state, per the cross-firing budget at lines 166-170) and `PROVIDER_UNAVAILABLE` — five consecutive 5xx/timeout/connection-reset failures. The project's established convention, cited by this very document as out-of-scope context (line 199) and stated in slices 262/263/264 and runbook 100, is that a partial pass exits 3 and fails the unit *on purpose* so a degraded provider is visible rather than silent. Failure scenario: EODHD begins returning 5xx at 01:20 UTC after the trailing phase completes; every subsequent firing that day ends `PROVIDER_UNAVAILABLE` after five symbols, exits 0, `mt-run status` shows the minute pass green, and the backfill of the 94k-row repair backlog silently stops draining — visible only as a journal line nobody is obliged to read. Either give `PROVIDER_UNAVAILABLE` a non-zero exit regardless of phase, or state explicitly why this pass departs from the exit-3 convention the rest of the system uses.

### [CONCERN] Interaction with slice 912's cycle-end stamping on an aborted pass is unstated

Frontmatter lists `interfaces: [912]` and the Component table changes `data/acquisition/daemon/minute.py`, but neither the Dependencies section (lines 204-219) nor the Data Flow abort step says what an aborted pass does to slice 912's `last_minute_cycle_end_utc` — the stamp 912 introduced as the cadence gate and the `--stop-when-done` termination condition — or to `acquisition_state.last_attempt_outcome` for symbols in the un-attempted tail. The "no response, no accounting" rule (lines 145-151) is scoped to `attempt_count` and `fetch_status` on `data_gaps` rows only. Failure scenario: a 402 abort at 01:06 UTC stamps `last_minute_cycle_end_utc` as if the cycle completed; a hand-run `mt data daemon run --minute --stop-when-done` later that morning sees a satisfied cadence gate and exits reporting drained work without fetching anything — 912's issue #6 behavior reintroduced through a path 912 never contemplated. Name the stamping behavior for each `MinutePassOutcome`.

### [NOTE] Two documented deviations from the 900 slice-plan entry

The plan entry (900-slices.foundation-cleanup.md:72) specifies scope (1) as "minute session ranges end at the UTC midnight after the last session's date" and scope (4) as thresholds "against a trailing baseline". The design instead ends the range at `session_close_utc` (conforming to 140 §Gap function step 6, verified against the architecture) and pushes midnight to a fetch-layer provider window, and replaces the trailing baseline with absolute floors. Both deviations are the better call — the plan's midnight end would have broken `coalesce_data_gaps._are_adjacent` (correctly identified at line 244), and a trailing baseline would certify the broken 45k state (Decision 5) — and both are recorded in the Review Response table. Noting only so the plan entry gets reconciled rather than left contradicting the design it points at.

### [NOTE] Signature extensions to 140-owned functions are not escalated the way the daily-path deviation is

The 900 architecture requires that a maintenance slice "consumes the interfaces its target layer already publishes … It does not redefine them. A fix that requires changing a contract is escalated to the owning initiative" (900-arch:27). The slice honors this for the daily-path range end (escalated to 140, Out of scope line 200-202), but it also adds a per-symbol uncovered-days parameter to `compute_missing_minute_sessions` and an optional `min_gap_end` filter to `actionable_gap_selector` — both 140-owned surfaces — without the same explicit escalation note. These are additive, backward-compatible parameters serving a corrective fix, so this reads as within the band's latitude rather than a violation; it is worth one sentence saying so, so a later reader does not have to re-derive the judgment.

### [PASS] Dependency direction and maintenance-band placement are correct

The slice depends on 919 (same band) and consumes — rather than reopens — slices 145, 162, and 165, which is exactly the arrangement the 900 architecture's maintenance-band extension sanctions (900-arch:22-29): a maintenance slice comes after the work it corrects and may touch the acquisition and data-quality layers another initiative delivered. The "prerequisite for 100-180" dependency statement applies to the 900-909 foundation slices, not here, and the slice does not claim otherwise.

### [PASS] Failure modes for the new I/O paths are enumerated with explicit handling, not TBD

Every provider outcome has a stated policy: 402 aborts the pass, 429 exhaustion/5xx/timeout/connection reset touch no gap row and count toward a five-failure breaker, and 200/404 are the only states that move accounting. The repair script's failure modes are equally concrete — advisory-lock contention blocks for `DAEMON_LOCK_TIMEOUT` then skips and reports the symbol, per-symbol commits make a mid-run death resumable, a second `--apply` is a no-op, and it refuses to start while a pass unit is active. Success criteria 4 and 5 bind each mode to a unit test. The one deferred item (the exact current accounting path that promoted 7,755 rows) is bounded by a general rule plus a reproducing test rather than left as an open question.

### [PASS] Every reader of the changed value is analyzed before the change

The "Consumers of minute `gap_end`" table walks the coalescer, the selector, the single writer, the chunk loop, the frontier gate, the API response models (184/185/187), and the CLI/rendering readers, stating for each why the 13:30→20:00 move is safe. The coalescer entry in particular shows the design reasoning about why the range end is the session close and not midnight, which is what makes the 140-conformance choice defensible rather than incidental.

### [PASS] No magic strings; new dispatch values are typed and centralized

`MinutePassOutcome` is a `StrEnum` beside `LastAttemptOutcome` in `state.py`, and all six new tunables (`MINUTE_TRAILING_PRIORITY_WINDOW`, `MINUTE_PASS_MAX_CONSECUTIVE_PROVIDER_FAILURES`, the five `HEALTH_MINUTE_SESSION_*`, `REPAIR_921_WINDOW_START`) land in `constants.py` with their measured justification. This satisfies the 900 architecture's "No magic strings" principle and 919's "one named threshold in `constants.py`" pattern.
