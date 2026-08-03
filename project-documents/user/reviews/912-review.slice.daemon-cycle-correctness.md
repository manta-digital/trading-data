---
docType: review
layer: project
reviewType: slice
slice: daemon-cycle-correctness
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/912-slice.daemon-cycle-correctness.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260803
dateUpdated: 20260803
findings:
  - id: F001
    severity: pass
    category: principles
    summary: "Directly applies the \"no magic strings\" principle"
    location: project-documents/user/slices/912-slice.daemon-cycle-correctness.md#d4--idle-reasons-are-an-enum-and-the-exit-message-states-which-one
  - id: F002
    severity: pass
    category: error-handling
    summary: "Failure modes enumerated for the new I/O/wait path"
    location: project-documents/user/slices/912-slice.daemon-cycle-correctness.md#d5--a---stop-when-done-run-satisfies-a-cadence-gate-by-sleeping-not-by-exiting
  - id: F003
    severity: pass
    category: principles
    summary: "Aligns with the explicit-failure / clean-codebase principle"
    location: project-documents/user/slices/912-slice.daemon-cycle-correctness.md#d3--split-late_bar_grace_period-into-daily_cycle_start_offset
  - id: F004
    severity: pass
    category: principles
    summary: "Aligns with structured logging expectations"
    location: project-documents/user/slices/912-slice.daemon-cycle-correctness.md#d5--a---stop-when-done-run-satisfies-a-cadence-gate-by-sleeping-not-by-exiting
  - id: F005
    severity: concern
    category: scope
    summary: "Slice scope goes beyond the parent architecture's stated scope"
    location: project-documents/user/slices/912-slice.daemon-cycle-correctness.md#scope
  - id: F006
    severity: concern
    category: dependencies
    summary: "Dependency direction requires acquisition-layer interfaces from foundation work"
    location: project-documents/user/slices/912-slice.daemon-cycle-correctness.md#interfaces-required
---

# Review: slice — slice 912

**Verdict:** CONCERNS
**Model:** minimax/minimax-m3

## Findings

### [PASS] Directly applies the "no magic strings" principle

D4 introduces `RunnerIdleReason` `StrEnum` (`NOTHING_DUE`, `NO_ACTIONABLE_WORK`) in place of the prior `did_anything` boolean, explicitly grounding the choice in the project's no-magic-strings rule. This is a clean, well-motivated application of the architecture's stated principle.

### [PASS] Failure modes enumerated for the new I/O/wait path

D5 introduces a new long-sleep behavior and explicitly handles its failure modes: Ctrl-C latency is bounded to `cap_seconds` (60 s) via the existing `sleep_until_next_due_event`, the PEP 475 / `_should_exit` flag polling semantics are spelled out, and an INFO log announces the wait with reason and due time to prevent a silent stall from being mistaken for a hang. D6 similarly bounds per-cycle noise by counting rather than per-symbol warning (906-per-pass is what made the current behavior invisible). Good practice for a new blocking path.

### [PASS] Aligns with the explicit-failure / clean-codebase principle

D3 separates `LATE_BAR_GRACE_PERIOD` (session-close meaning, used by migration 043 and `data_status`) from the new `DAILY_CYCLE_START_OFFSET` (UTC-midnight offset used by `runner.py`). Two concepts that were sharing one value by coincidence now have independent docstrings; the slice notes "The values are equal today; nothing may rely on that." This is precisely the kind of separation the architecture's clean-codebase goal targets.

### [PASS] Aligns with structured logging expectations

D5 logs at INFO with a named reason and due time on entering the wait, and the `--stop-when-done` exit path is rewritten to surface the idle reason instead of the prior conflated boolean. Both are consistent with the architecture's structured-logging goal and the `get_logger(__name__)` pattern, and the slice avoids introducing new magic log-message variants.

### [CONCERN] Slice scope goes beyond the parent architecture's stated scope

The parent architecture (`900-arch.foundation-cleanup.md`) scopes the initiative to "CLI framework, configuration system, logging, provider registry, deprecated code removal, and project packaging," and frames 900 as work that "produces the foundation that initiatives 100-180 build upon." Acquisition work is implicitly downstream. This slice substantively rewrites `daily_cycle_due`, `RunnerState`, `sleep_until_next_due_event`, and `_loop` in `data/acquisition/daemon/runner.py`, plus the daily cycle's work-list derivation in `data/acquisition/daemon/daily.py`. Those are acquisition-layer modules whose correctness ownership belongs to a different initiative band. The slice's own `Notes` section recognizes the discipline concern by declining to delete `data/acquisition/daily/freshness.py` here — yet applies the inverse of that discipline by performing acquisition correctness work inside foundation cleanup. The "maintenance band" justification is internal to the slice and not visible in the architecture's scope statement; this placement should be reconciled with the architecture owner.

### [CONCERN] Dependency direction requires acquisition-layer interfaces from foundation work

The slice's required interfaces are 145 (`run_daily_cycle`'s `data_gaps`-driven contract, pool-per-cycle ownership, `update_data_gaps` as the single writer of `acquisition_state`), 154 (`mt data daemon run` CLI surface), and 168 (`assert_cagg_fresh` precedent). Interfaces 145 and 168 are acquisition-layer contracts. The architecture explicitly states foundation cleanup is a prerequisite for initiatives 100-180 (which presumably own acquisition), and only authorizes integration with the existing pipeline via the CLI — not depending on acquisition contracts to drive gate logic. This is a layering direction the architecture document does not explicitly sanction and is worth confirming with the architecture owner, particularly given that interface 168 is consulted only as a precedent for D1's rejected alternative and could in principle be dropped from the dependency list.
