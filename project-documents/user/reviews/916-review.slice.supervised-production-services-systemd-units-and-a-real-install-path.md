---
docType: review
layer: project
reviewType: slice
slice: supervised-production-services-systemd-units-and-a-real-install-path
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/slices/916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md
aiModel: z-ai/glm-5.2
status: complete
dateCreated: 20260822
dateUpdated: 20260822
reviewedSha: 919df5803928781d2f8b7f82749075128b78e3ea
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Architectural principles honored throughout"
    location: "916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md#Architecture"
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Failure modes enumerated for the new I/O path"
    location: "916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md#Install-script-failure-recovery"
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Over-engineering resisted; complexity deferred with recorded rationale"
    location: "916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md#Deferred-—-cross-source-arbitration-needs-its-own-slice"
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Maintenance band scope is appropriate"
    location: "916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md#Overview"
  - id: F005
    severity: note
    category: uncategorized
    summary: "No architectural NFRs to restate"
    location: "900-arch.foundation-cleanup.md#Design-Goals"
  - id: F006
    severity: pass
    category: uncategorized
    summary: "Dependency directions and integration points are correct"
    location: "916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md#Integration-Points"
---

# Review: slice — slice 916

**Verdict:** PASS
**Model:** z-ai/glm-5.2

## Findings

### [PASS] Architectural principles honored throughout

The slice consistently applies the architecture's "explicit failure" principle: the environment file is the single config source and fails explicitly if a variable is missing; the maintenance credential is deliberately absent from service environments; the install script aborts on mismatched accounts rather than silently adopting them. The "no silent fallback" rule is respected — services read only `/etc/manta-trading.env`, never fall back to a dev `.env`. The "CLI is the verification surface" principle is preserved: success criteria include both `systemctl status` and `acquisition_state` queries, keeping `mt` as a verification path alongside the new systemd surface.

### [PASS] Failure modes enumerated for the new I/O path

The install script (`install-production.sh`) is the slice's only substantial new I/O path, and its failure modes are enumerated per-step with explicit handling strategies: failed `git clone` leaves a partial tree that is cleaned on re-run; failed `uv sync` leaves `.venv` in place for reconciliation (not deleted); `daemon-reload` runs unconditionally at the end of every run so "files installed but reload skipped" is unreachable; env files are never overwritten. Each failure state is named, not left as "TBD."

### [PASS] Over-engineering resisted; complexity deferred with recorded rationale

The slice explicitly defers three forward-looking mechanisms — cross-source arbitration/preemption, resource weights on `manta-acquisition.slice`, and hand-staggered schedules — each with a stated reason it cannot be settled now and a record in the slice plan. The `manta-acquisition.slice` unit ships empty (grouping, not tuning), and the naming pattern is a documented procedure rather than a generalized framework. This aligns with the architecture's "resist adding complexity" principle.

### [PASS] Maintenance band scope is appropriate

The architecture's maintenance band permits touching any layer but constrains work to "corrective, not additive" and "originating initiative's contracts honored, not rewritten." This slice completes slice 128's never-installed systemd templates (corrective), closes three explicitly documented "Not yet documented" gaps (corrective), and consumes pass semantics from 145/146/912 and credential separation from 913 without redefining them (contracts honored). The dedicated `/opt` install path is new infrastructure, but it corrects the operational deficiency the slice plan entry identified — production running from a mutable dev checkout with no crash supervision.

### [NOTE] No architectural NFRs to restate

The architecture document does not state specific NFR targets (latency, throughput, availability). The slice's operational constraints (journald 2 GiB / 200 MiB caps, `TimeoutStopSec=300`, `StartLimitBurst=5`) originate from slice 128 and operational judgment, not from architectural NFRs. The NFR-restating criterion does not apply because no NFRs are stated in the parent architecture for paths this slice touches.

### [PASS] Dependency directions and integration points are correct

The slice consumes from completed slices (128, 908, 145/146/912, 913) without modifying their contracts, and provides supervised production infrastructure to future slices (notably 907/CI). Dependency direction flows downward from the completed foundation slices. The `EnvironmentFile=` injection path matches the architecture's Settings design (pydantic-settings reads `MT_*` from process environment), and the `.env`-in-CWD convenience is explicitly scoped to dev only.
