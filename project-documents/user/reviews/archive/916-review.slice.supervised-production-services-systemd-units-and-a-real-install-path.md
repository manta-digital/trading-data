---
docType: review
layer: project
reviewType: slice
slice: supervised-production-services-systemd-units-and-a-real-install-path
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260822
dateUpdated: 20260822
reviewedSha: 24ce883bcaeebbcffa8aa35b4099cc4a47b32ae9
findings:
  - id: F001
    severity: concern
    category: architectural-alignment
    summary: "Production liveness answer moves off the CLI, against a stated Design Goal"
    location: "project-documents/user/slices/916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md:33-35"
  - id: F002
    severity: concern
    category: scope-boundary
    summary: "New capability delivered under the maintenance band's \"corrective, not additive\" constraint"
    location: "project-documents/user/slices/916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md:17-29"
  - id: F003
    severity: note
    category: failure-modes
    summary: "Install-script network I/O failure modes are not enumerated"
    location: "project-documents/user/slices/916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md:235-241"
  - id: F004
    severity: pass
    category: dependency-direction
    summary: "Contract boundaries with dependency slices are honored, not rewritten"
    location: "project-documents/user/slices/916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md:118-131"
  - id: F005
    severity: pass
    category: architectural-alignment
    summary: "Explicit-failure and no-magic-defaults principles are correctly inherited"
    location: "project-documents/user/slices/916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md:146-148"
---

# Review: slice — slice 916

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] Production liveness answer moves off the CLI, against a stated Design Goal

`900-arch.foundation-cleanup.md` states as an Architectural Principle: "**CLI is the verification surface**: If a feature can't be exercised through `mt`, it doesn't exist yet. Every initiative's work must be visible through CLI commands before it's considered complete" (900-arch.foundation-cleanup.md:53). Slice 916's Value section explicitly does the opposite for this capability: "The answer to 'is production running?' becomes `systemctl status` / `systemctl list-timers` **instead of** querying `acquisition_state` by hand" (916 doc:34-35), and every success criterion (1-6) and the verification walkthrough are `systemctl`/`journalctl`-based, with no corresponding `mt status`/`mt data daemon status` surface added. Existing `mt status` (from slice 902) already reports provider/DB health; this slice introduces a new operational dimension (is the supervisor running, last/next fire time) without extending that CLI surface, leaving the answer to "is production alive" reachable only by a host shell, not `mt`. Worth an explicit PM call on whether this is an accepted exception (host supervision is arguably outside "features" the CLI enumerates) or a gap to close (e.g., a thin `mt status` addition surfacing systemd state) before calling the slice complete.

### [CONCERN] New capability delivered under the maintenance band's "corrective, not additive" constraint

The architecture's maintenance-band scope extension (900-arch.foundation-cleanup.md:22-29) permits band 900-999 to touch any layer, but gates that on two constraints: "**Corrective, not additive.** ... New capability belongs to the initiative that owns the layer" and "the originating initiative's contracts are honored, not rewritten." Systemd supervision and a dedicated `/opt` install path are not a fix to already-specified-but-wrong behavior — they are genuinely new capability, and the parent slice-plan entry itself says as much: it "**Absorbs** initiative 180's 'Supervised process launcher' future-work item," i.e., work that a different, non-maintenance initiative was tracking (900-slices.foundation-cleanup.md:62, entry 17). The plan justifies the absorption ("one piece of work seen from two ends, since `ExecStart=` cannot be written without first deciding what is installed where"), and that reasoning is defensible given 908's checkout-vs-install entanglement — but it is a plan-level exception to the architecture's own explicit anti-scope-creep rule, made without the architecture document itself being amended to reflect it. Recommend either a short amendment/pointer in 900-arch.foundation-cleanup.md's scope-extension section acknowledging this class of absorption, or explicit PM sign-off recorded in the slice doc (beyond the plan entry) that this is a deliberate, bounded exception rather than a precedent for routing initiative-owned features through the maintenance band generally.

### [NOTE] Install-script network I/O failure modes are not enumerated

The migration plan's step 1 (`deploy/install-production.sh`) performs new I/O (git clone from a remote, `uv sync` package resolution/download) and states the script is "idempotent (safe to re-run) and refuses to proceed if `/opt/manta-trading` exists with local modifications" — but doesn't say what happens on a partial failure mid-run (clone interrupted, `uv sync` fails after clone succeeds, network hang during either). Given it's a PM-supervised, foreground, one-shot script (not a background service), the operational risk is low — a hang just blocks the terminal and can be interrupted — but the design criteria call for explicit handling of new I/O paths rather than relying on "idempotent, re-runnable" as an implicit catch-all. A one-line note on what state a half-completed run leaves behind and that re-running is the prescribed recovery would close this cleanly.

### [PASS] Contract boundaries with dependency slices are honored, not rewritten

Pass semantics from 145/146/912 (`--stop-when-done`, gate wait, cheap re-run, resumability), credential separation from 913 (maintenance URL deliberately excluded from the service environment), and 908's checkout-based-production decision (D7) are all consumed as-is and cited by decision number rather than reinterpreted — consistent with the architecture's maintenance-band rule that "a maintenance slice consumes the interfaces its target layer already publishes ... and may depend on them freely. It does not redefine them" (900-arch.foundation-cleanup.md:27).

### [PASS] Explicit-failure and no-magic-defaults principles are correctly inherited

The single-source environment file design ("No `.env` file is placed in `/opt/manta-trading`... so there is exactly one source (fails explicit if a variable is missing, per project rules)") directly implements the architecture's "Explicit failure" principle (900-arch.foundation-cleanup.md:51) and the project's no-silent-fallback rule, and the fixed `DAILY_DAEMON_ID`/`MINUTE_DAEMON_ID` constants (916 doc:124-127) are consistent with the architecture's "No magic strings" principle (900-arch.foundation-cleanup.md:49).
