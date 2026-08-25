---
docType: review
layer: project
reviewType: slice
slice: collection-pass-and-supervised-install
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/slices/263-slice.collection-pass-and-supervised-install.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260825
dateUpdated: 20260825
reviewedSha: a599f0e08341d193e28b9c0d36283c508dc607a2
findings:
  - id: F001
    severity: pass
    category: alignment
    summary: "Phase sequencing enforces the catalog-first principle in code, not convention"
    location: "project-documents/user/slices/263-slice.collection-pass-and-supervised-install.md:100"
  - id: F002
    severity: pass
    category: alignment
    summary: "CLI/timer single-code-path principle preserved"
    location: "project-documents/user/slices/263-slice.collection-pass-and-supervised-install.md:112"
  - id: F003
    severity: pass
    category: nfr-restatement
    summary: "Timer cadence restates the parent's NFR-shaped guidance with concrete numbers"
    location: "project-documents/user/slices/263-slice.collection-pass-and-supervised-install.md:118"
  - id: F004
    severity: pass
    category: error-handling
    summary: "Termination/failure modes for the new operational surface are enumerated, not TBD"
    location: "project-documents/user/slices/263-slice.collection-pass-and-supervised-install.md:118-128"
  - id: F005
    severity: note
    category: process
    summary: "Decision 8 leaves one criterion's fate to a post-design PM veto"
    location: "project-documents/user/slices/263-slice.collection-pass-and-supervised-install.md:126"
  - id: F006
    severity: note
    category: architectural-boundary
    summary: "Shared `mt-run` wrapper fix crosses the Kalshi/EODHD boundary"
    location: "project-documents/user/slices/263-slice.collection-pass-and-supervised-install.md:122"
---

# Review: slice — slice 263

**Verdict:** PASS
**Model:** claude-sonnet-5

## Findings

### [PASS] Phase sequencing enforces the catalog-first principle in code, not convention

The architecture's first Architectural Principle ("The catalog is the spine; time series hang off it… catalog sync completes before the time-series surfaces run") is explicitly enforced by the abort rule in `CollectionPass.run()`: a `PROVIDER_ABORT`/`STORAGE_ABORT` phase halts remaining phases and marks them `skipped`. The slice doc even cites the architecture text directly when describing this as enforced "here, not by convention," which is the correct way to carry a parent constraint into a design that only implements the first phase.

### [PASS] CLI/timer single-code-path principle preserved

Architecture principle "CLI is the baseline, the timer is the target… One code path, no state divergence" (260-arch line 53) is satisfied by Decision 1: `sync` and `pass` share the extracted `KalshiRun` preflight context, and the design explicitly rejects folding `sync` into `pass --only catalog` because it would let operator options leak onto the timer's fixed invocation — correctly protecting the no-divergence guarantee rather than just asserting it.

### [PASS] Timer cadence restates the parent's NFR-shaped guidance with concrete numbers

The architecture leaves "the timer interval… should be chosen so steady-state passes are short and frequent" as an open Technical Consideration (260-arch line 102) and explicitly rejects tight/near-real-time cadence elsewhere (slice-plan Future Work #3). Decision 4 restates this with an actual target — hourly at `:20`, steady-state ≈2–3 min at the 300/min public budget, ~10k requests/day — and ties the choice back to the ADR that reserves tight cadence for the streaming form. This is the kind of specific-target restatement the review criteria ask for, not a hand-wave.

### [PASS] Termination/failure modes for the new operational surface are enumerated, not TBD

Decisions 4, 5, and 9 walk through the concrete failure modes this slice's new I/O/process surface introduces — timer overlap (systemd won't restart a still-running unit), SIGTERM mid-run (default disposition, journal signature `15/TERM`, why no cooperative shutdown is needed given 262's per-page/per-window transaction boundaries), and lock-held contention (preflight exit 1, visible in `mt-run status`, retried next firing) — each with an explicit handling strategy and rationale for what was rejected (e.g., a runner-style stop flag). No "TBD" placeholders remain on these paths.

### [NOTE] Decision 8 leaves one criterion's fate to a post-design PM veto

The per-window INFO line (Decision 8) and Success Criterion 11 are written as "PM may veto… in which case this criterion is struck." Both branches are fully specified (line logs one way or the criterion is removed), so this isn't a true under-specified TBD, and it traces directly to the slice-plan's own "Potential for 263" note. Still worth flagging: it's an open approval gate inside an otherwise-closed design doc rather than a decision resolved before review.

### [NOTE] Shared `mt-run` wrapper fix crosses the Kalshi/EODHD boundary

Decision 6 fixes a pre-existing bug in `mt-run`'s root branch (it forwarded only two `MT_*` variables) — this is the one change in the slice that touches shared production tooling used by all sources, not Kalshi-isolated code, which brushes against the architecture's "Isolation from the core pipeline" goal (260-arch line 37). The doc handles this well: it's called out explicitly for the reviewer, the non-root branch is left untouched, and Success Criterion 6 plus the Risk Assessment section add an explicit EODHD regression check (`mt-run data caggs status` still sees `MT_EODHD_API_KEY`). No action needed — noting for visibility since it's the one place this slice's blast radius extends past Kalshi.
