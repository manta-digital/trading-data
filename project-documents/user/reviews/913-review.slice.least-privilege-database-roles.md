---
docType: review
layer: project
reviewType: slice
slice: least-privilege-database-roles
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/slices/913-slice.least-privilege-database-roles.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260806
dateUpdated: 20260806
reviewedSha: 381ea74dd873fdc53304ba0d26363b06333e8c05
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Maintenance band alignment — corrective, not additive"
    location: 913-slice.least-privilege-database-roles.md#overview
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Centralized configuration pattern honored"
    location: 913-slice.least-privilege-database-roles.md#d4--settings-gains-a-maintenance-key-resolution-stays-in-the-caller
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Explicit-failure principle consistently applied"
    location: 913-slice.least-privilege-database-roles.md#d4--settings-gains-a-maintenance-key-resolution-stays-in-the-caller
  - id: F004
    severity: pass
    category: uncategorized
    summary: "CLI is the verification surface"
    location: 913-slice.least-privilege-database-roles.md#success-criteria
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Idempotency of the provisioning artifact"
    location: 913-slice.least-privilege-database-roles.md#d6--grants-as-an-idempotent-re-runnable-artifact
  - id: F006
    severity: pass
    category: uncategorized
    summary: "No new dependencies, no magic strings introduced"
    location: 913-slice.least-privilege-database-roles.md#technical-decisions
  - id: F007
    severity: pass
    category: uncategorized
    summary: "`trading_migrate` ownership-adjacent strategy avoids scope creep"
    location: 913-slice.least-privilege-database-roles.md#d1--two-roles-ownership-unchanged
  - id: F008
    severity: pass
    category: uncategorized
    summary: "`mt data status` misclassification surfaced and resolved in-band"
    location: 913-slice.least-privilege-database-roles.md#d3--write-surface-is-enumerated-not-inferred
  - id: F009
    severity: note
    category: uncategorized
    summary: "Default-privileges coverage assumes role creation"
    location: 913-slice.least-privilege-database-roles.md#risks
  - id: F010
    severity: note
    category: uncategorized
    summary: "Failure-mode handling for runaway/timeout of the provisioning run is implicit"
    location: 913-slice.least-privilege-database-roles.md#migration-plan
  - id: F011
    severity: note
    category: uncategorized
    summary: "Verification Walkthrough is marked Draft"
    location: 913-slice.least-privilege-database-roles.md#verification-walkthrough
  - id: F012
    severity: note
    category: uncategorized
    summary: "Source-code line references cannot be verified from the architectural review set"
    location: 913-slice.least-privilege-database-roles.md#d5--runnerpy-autocommit-path-inherits-the-same-url
---

# Review: slice — slice 913

**Verdict:** PASS
**Model:** minimax/minimax-m3

## Findings

### [PASS] Maintenance band alignment — corrective, not additive

The slice fixes a documented defect (the 2026-08-04 TRUNCATE incident) by enforcing the existing `sql.md` "Production Database Protection → Split connection roles" rule rather than introducing new product capability. This is exactly the maintenance-band invariant the architecture document describes ("Corrective, not additive"; "originating initiative's contracts are honored, not rewritten"). Scope is intentionally bounded to grants + a runtime URL split.

### [PASS] Centralized configuration pattern honored

`MT_TIMESCALE_MAINTENANCE_URL` is added to the existing `Settings` (pydantic-settings) class with the correct `MT_` prefix, alongside the existing `timescale_db_url`. The architecture reserves TOML for persistent preferences and environment vars for credentials/runtime config (Architecture: `Settings` vs. `CONFIG_KEYS/ConfigManager`; "Credentials never go here"). The slice correctly puts the new credential in `Settings`, not in any TOML layer.

### [PASS] Explicit-failure principle consistently applied

The architecture mandates "Never silently fall back to a default that masks a misconfiguration." The slice not only follows this in D4 ("fails loudly ... never a silent fallback to `timescale_db_url`, which would restore exactly the coupling this slice removes") but also makes it a testable success criterion and an explicit step (Step 6) in the verification walkthrough. The phrase is also in the architecture ("missing required values raise immediately"); the slice's "explicit error naming the missing key" matches that style.

### [PASS] CLI is the verification surface

The architecture states "If a feature can't be exercised through `mt`, it doesn't exist yet." The slice's success criteria and Verification Walkthrough exclusively use `mt data …` commands (status, migrate apply, pull, get, caggs status, restore run, rechunk, caggs repair, caggs refresh) plus the API surface when the daemon is wired to the new credential. No hidden/private APIs are introduced.

### [PASS] Idempotency of the provisioning artifact

`provision_roles.sql` is explicitly required to be re-runnable, with the role-creation step wrapped in a `DO` block guard so repeat runs do not error. The Verification Walkthrough steps 1 (run, then run again) and Step 5 (DDL under wrong credential must fail) cover both the idempotency and the failure-path invariants. This addresses the partial-execution / repeat-run failure mode that the SQL provisioning I/O path would otherwise expose.

### [PASS] No new dependencies, no magic strings introduced

The change set is purely SQL + a new `Settings` field + caller-side URL resolution. No new libraries are added, which is consistent with the architecture's "Minimal new dependencies" principle. Identifiers (env var name, role names, CLI command paths) are explicit constants, not string-dispatched.

### [PASS] `trading_migrate` ownership-adjacent strategy avoids scope creep

The slice explicitly elects `GRANT postgres TO trading_migrate` over per-object `ALTER ... OWNER`. This preserves ownership contracts of the originating initiative (100-180 layers) and avoids a large schema-rewrite that would violate the "contracts are honored, not rewritten" maintenance invariant. Catalog visibility was verified empirically against prod to justify this simplification.

### [PASS] `mt data status` misclassification surfaced and resolved in-band

A latent "read-only" mislabel on `mt data status` (it actually writes `trading_sessions` via the auto-extend hook) is caught by enumerating the write surface rather than inferred from dispatch. The slice explicitly notes this is "not a defect to fix here" because the application role's DML grant on `trading_sessions` covers it; deferring the operator-surface correction to the owning slice is the correct maintenance-band discipline.

### [NOTE] Default-privileges coverage assumes role creation

The Risks section correctly identifies that `ALTER DEFAULT PRIVILEGES FOR ROLE postgres` only affects objects `postgres` itself creates and calls out that `trading_migrate`-created tables must also be covered. The note says "Both roles are covered in the artifact." This is correct, but the cross-reference to the relevant `ALTER DEFAULT PRIVILEGES` block within `provision_roles.sql` would strengthen the design — a future reader might otherwise miss whether both rows are actually present. Worth a brief mention rather than a defect.

### [NOTE] Failure-mode handling for runaway/timeout of the provisioning run is implicit

Failure modes for the new SQL provisioning I/O path (psql session timeout, peer disconnect mid-`ALTER DEFAULT PRIVILEGES`, interruption after role creation before grants) are handled only by idempotency + a one-line rollback at the deploy layer. The retry/restore mechanism is real but lives implicitly inside "purely additive; idempotent re-runnable" rather than being stated as an explicit strategy for each new message type the artifact emits. The architecture doesn't impose NFRs on database provisioning specifically, and maintenance-band slices tend to be conservative on out-of-scope behavior, so this is informational only.

### [NOTE] Verification Walkthrough is marked Draft

The Walkthrough is explicitly "Draft — to be refined at Phase 6 completion." This is acceptable for a maintenance slice, but the placeholder placeholders (`<recent>` start/end timestamps, `$APP_URL` resolution) are not yet pinned. No contradiction with the architecture, only a reminder that this section will need to be closed out before merge.

### [NOTE] Source-code line references cannot be verified from the architectural review set

Citations such as `[runner.py:81]`, `[timescale_minute_db.py:203]`, `[migrate_cold_start.py:280]`, `[data.py:883]`, `[restore_metadata.py:249]` anchor the design to specific call-sites. These cannot be confirmed from the documents under review, but they are used to justify integration points rather than to claim contracts. Flagging only so a downstream reviewer opens the cited files before sign-off.
