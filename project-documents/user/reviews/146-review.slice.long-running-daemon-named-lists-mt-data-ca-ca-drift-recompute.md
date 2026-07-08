---
docType: review
layer: project
reviewType: slice
slice: long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/slices/146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute.md
aiModel: z-ai/glm-5.1
status: complete
dateCreated: 20260503
dateUpdated: 20260503
findings:
  - id: F001
    severity: concern
    category: scope-creep-alignment
    summary: "Bulk-EOD steady-state deferred despite architecture assigning it to slice 146"
    location: 146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute.md#overview
  - id: F002
    severity: concern
    category: under-specification
    summary: "`ca update` integration into unattended daemon flow is unspecified"
    location: 146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute.md#data-flows
  - id: F003
    severity: concern
    category: error-handling
    summary: "Missing failure mode: EODHD peer disconnect mid-send"
    location: 146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute.md#failure-modes
  - id: F004
    severity: note
    category: nfr-restatement
    summary: "NFR target discrepancy for single-symbol fast path"
    location: 146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute.md#non-functional-targets
  - id: F005
    severity: note
    category: specification-consistency
    summary: "Internal inconsistency: drift-check data flow vs. Decision C placement"
    location: 146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute.md#data-flows
  - id: F006
    severity: pass
    category: alignment
    summary: "CLI surface matches architecture specification exactly"
    location: 146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute.md#outputs
  - id: F007
    severity: pass
    category: alignment
    summary: "Token-bucket and quota design aligns with architecture constants"
    location: 146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute.md#approach
  - id: F008
    severity: pass
    category: alignment
    summary: "CA-drift detection and band recompute match architecture specification"
    location: 146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute.md#approach
  - id: F009
    severity: pass
    category: dependencies
    summary: "Dependency direction is correct; no circular or hidden dependencies"
    location: 146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute.md#cross-slice-dependencies
---

# Review: slice — slice 146

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.1

## Findings

### [CONCERN] Bulk-EOD steady-state deferred despite architecture assigning it to slice 146

The architecture document makes two explicit statements: (1) "Per-symbol `/eod` is used **only** for backfill and refetch — the steady-state daemon never makes per-symbol calls" (Backfill behavior → Daily section), and (2) in References: "slice 146 adds CA-detection and the **bulk-EOD steady-state path**." The slice defers bulk-EOD to slice 152 and ships per-symbol `/eod` in steady-state, which directly violates the architecture's behavioral constraint for the steady-state daemon. The justification (quota headroom, edge-case complexity) is reasonable, but the slice should either: (a) revise the architecture to formally reassign bulk-EOD to 152 with an acknowledged temporary deviation, or (b) include bulk-EOD as originally scoped. As written, the slice's steady-state daemon makes ~13k per-symbol calls daily — exactly the pattern the architecture says the steady-state daemon "never" uses.

---

### [CONCERN] `ca update` integration into unattended daemon flow is unspecified

The architecture states: "`mt data ca update` (no flags) typically runs once per UTC day — either via cron/systemd timer, or as an inline once-per-day guarded action inside the long-running daemon's main loop. Either is fine; implementation chooses the one with lower operational surface." The slice makes no choice. The data flow for the long-running loop shows `drift_check_due()`, `daily_cycle_due()`, `minute_cycle_due()`, and `sleep_until_next_due_event()` — but no `ca_update_due()` step. Without CA ingestion in the unattended flow, CA-drift detection can never fire unless an operator manually runs `mt data ca update`, undermining the slice's stated value that "CA correctness is self-healing" and "one unattended process replaces a cron jungle." The slice must either: (a) add a once-per-day `ca update` step to the daemon loop, or (b) explicitly document that operators must run `ca update` via external scheduling and acknowledge this limits the self-healing claim.

---

### [CONCERN] Missing failure mode: EODHD peer disconnect mid-send

The Failure Modes section covers EODHD HTTP 429, 5xx, 4xx, and "Network timeout" but does not address peer disconnect mid-response (TCP connection drops after partial data received). This is distinct from a timeout: the response has begun arriving, partial payload may be in buffer, and the data could be truncated JSON. The handling strategy must be explicit — e.g., discard partial response, treat as transient failure, retry per slice 145 policy. The review criteria specifically call out "peer disconnect mid-send" as a required failure-mode enumeration for each new I/O path.

---

### [NOTE] NFR target discrepancy for single-symbol fast path

The architecture specifies "~90s of API time" for the SPY backfill single-symbol fast path. The slice restates this as "under 2 minutes wall clock (architecture spec)." These are different metrics (API time vs. wall clock) with different target values (90s vs. 120s). When restating an NFR from the parent architecture, the slice should either match the architecture's metric and target, or explicitly acknowledge the difference and justify the relaxed bound (e.g., "wall clock includes token-bucket throttling overhead not captured by API-time metric").

---

### [NOTE] Internal inconsistency: drift-check data flow vs. Decision C placement

The Data Flows section shows `drift_check_due()?` as a separate top-level phase before `daily_cycle_due()` and `minute_cycle_due()`, iterating over each symbol independently. Decision C specifies drift detection as "per-symbol, at the top of each symbol's iteration in both `run_daily_cycle` and `run_minute_cycle`." These describe two different execution patterns — a separate drift-check sweep vs. integrated per-symbol drift checks within each cycle. If both were implemented, drift would be checked twice per symbol per cycle. The slice should resolve this to a single canonical design; Decision C's integrated approach is more consistent with the architecture's per-cycle drift detection spec.

---

### [PASS] CLI surface matches architecture specification exactly

The `mt data daemon run`, `mt data lists`, and `mt data ca` command surfaces — including all flags, mutual exclusivity of `--symbol`/`--list` on `ca update`, termination defaults for scoped vs. bare invocations, and the elimination of the legacy `adjustment` sub-app — all align precisely with the architecture's Operator commands section.

---

### [PASS] Token-bucket and quota design aligns with architecture constants

Decision A's two-window token bucket (`minute_window` capacity 1000, `day_window` capacity 100k rolling) with `CallType`-keyed cost lookup matches the architecture's Constants section (`EODHD_PER_MINUTE_BURST`, `EODHD_DAILY_QUOTA`, per-call costs). The rejection of persistence (bounded restart loss within `EODHD_PER_MINUTE_BURST`) and distributed coordination are justified given the single-process constraint.

---

### [PASS] CA-drift detection and band recompute match architecture specification

Decision C's per-symbol drift detection (comparing `last_adjusted_ca_snapshot_id` to current `compute_snapshot_id`), the recompute range `[min(changed_ca.ex_date), now()]`, the band-based UPDATE writer, and the advancement of `last_adjusted_ca_snapshot_id` only on success all directly implement the architecture's "Band-based adjustment writes" and `ca_snapshot` / `snapshot_id` specifications.

---

### [PASS] Dependency direction is correct; no circular or hidden dependencies

All hard dependencies point to earlier slices (142, 143, 144, 145). The slice's provided interfaces to downstream slices (147, 148, 149, 150, 152) are all consumption-oriented and introduce no reverse dependencies. Advisory lock coexistence with slice 148 (refetch) is correctly delegated to the slice 145 invariant.
