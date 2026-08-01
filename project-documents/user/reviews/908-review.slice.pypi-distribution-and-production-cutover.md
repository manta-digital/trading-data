---
docType: review
layer: project
reviewType: slice
slice: pypi-distribution-and-production-cutover
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/908-slice.pypi-distribution-and-production-cutover.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260801
dateUpdated: 20260801
findings:
  - id: F001
    severity: concern
    category: scope-completeness
    summary: "Distribution rename leaves architecture-defined config paths unaddressed"
    location: 908-slice.pypi-distribution-and-production-cutover.md#D1
  - id: F002
    severity: note
    category: error-handling
    summary: "PyPI publish failure modes are not enumerated beyond \"irreversible per version\""
    location: 908-slice.pypi-distribution-and-production-cutover.md#D9
  - id: F003
    severity: note
    category: error-handling
    summary: "D2's constant does not tighten the `mt --version` fallback against post-cutover misconfiguration"
    location: 908-slice.pypi-distribution-and-production-cutover.md#D2
  - id: F004
    severity: pass
    category: principle-adherence
    summary: "Strong alignment with \"no magic strings\" and squadron modeling"
    location: 908-slice.pypi-distribution-and-production-cutover.md#D2
  - id: F005
    severity: pass
    category: integration-points
    summary: "Dependency directions and integration points are correctly sequenced"
    location: 908-slice.pypi-distribution-and-production-cutover.md#Cross-slice-dependencies-and-interfaces
  - id: F006
    severity: pass
    category: error-handling
    summary: "Cutover failure handling is appropriately concrete for the I/O paths it owns"
    location: 908-slice.pypi-distribution-and-production-cutover.md#D7
---

# Review: slice — slice 908

**Verdict:** CONCERNS
**Model:** minimax/minimax-m3

## Findings

### [CONCERN] Distribution rename leaves architecture-defined config paths unaddressed

The architecture explicitly defines user config at `~/.config/manta-trading/config.toml` and project config at `.manta-trading.toml` in project root as part of the "Envisioned State" for Config Layer (architecture doc, "Config Layer" section). D1 renames the distribution `manta-trading` → `manta-trading-data` and explicitly defers the import-package rename to slice 911, but the slice never states what happens to these two config paths. Three readings are possible and the slice is silent on which it intends: (a) paths stay under `manta-trading` because they are tied to the import-package identity (which is still `manta_trading`), (b) paths migrate to `manta-trading-data` to track the distribution name, or (c) paths migrate in 911 alongside the import rename. Each reading has different consequences for existing users on .144 (who will have config at `~/.config/manta-trading/`) and for the `mt config path` output the architecture mandates. The slice should explicitly state which paths are in scope and how existing-user config is handled — particularly because D6 sequences the production cutover as a checkpoint, and a user-config mismatch on first tool install is exactly the kind of "silent fallback" the architecture's "Explicit failure" principle is meant to prevent.

### [NOTE] PyPI publish failure modes are not enumerated beyond "irreversible per version"

D9 specifies failure handling for the TestPyPI step (`continue-on-error: true`, `skip-existing: true`) but the actual PyPI publish job has only the risk-section statement "Irreversible per version" — not a concrete handling strategy for publish failure. The architecture's "Explicit failure" principle expects failures to be enumerated with explicit handling. Concrete failure modes worth distinguishing: (1) OIDC token validation fails (e.g., environment key added later, branch protection change) — what surfaces, what gets retried; (2) name collision (someone else uploaded to `manta-trading-data` between the 2026-08-01 verification and the first push); (3) network/registry outage mid-upload — partial state, retry semantics; (4) tag-gated workflow doesn't fire on push — what surfaces, who notices. The slice currently relies on "publish another version" as the recovery for any failure mode, which is correct for content errors but underspecified for infrastructure failures where the upload may have partially succeeded.

### [NOTE] D2's constant does not tighten the `mt --version` fallback against post-cutover misconfiguration

The architecture sanctions the "dev" fallback "if metadata unavailable (pre-install)" — D2 correctly identifies that this same fallback currently masks a stale literal on rename, and the constant fix is the right structural answer. What is not addressed: if the constant is ever wrong (typo, incomplete find-and-replace), `mt --version` on an installed package will silently report `dev` rather than failing — exactly the kind of silent fallback the "Explicit failure" principle opposes. The architecture's fallback language covers the pre-install case; the slice does not propose distinguishing "metadata unavailable because not installed" from "metadata unavailable because constant is wrong." A `logger.warning(...)` in the except branch, even if the fallback string remains, would be consistent with the principle.

### [PASS] Strong alignment with "no magic strings" and squadron modeling

D2 lifts `importlib.metadata.version("manta-trading")` from a literal at `cli/app.py:36` into a named constant in `constants.py`, directly instantiating the architecture's "No magic strings" principle: "All dispatch, status values, provider names, and command identifiers use enums or typed constants defined in one place." The slice explicitly cites the project rule that "a value used in lookups is defined once." D3 likewise cites the squadron `ci.yml` as the template, consistent with the architecture's "Model on Squadron" principle. Both decisions strengthen, rather than deviate from, the architecture.

### [PASS] Dependency directions and integration points are correctly sequenced

The dependency graph is consistent: 904 → 908 (packaging must exist before distribution), 908 → 907 (workflow file must exist before 907 extends it with lint/type/test), 908 → 909 (no `mt update` without a registry to check), 908 → 911 (import rename sequenced after 906's split per D1's reasoning). The interface to 907 — "must not rename or replace the workflow file" — is correctly honored by naming the new file `ci.yml` rather than `publish.yml` in D3, which is the choice that lets 907 extend one workflow. D8's explicit "must not pre-empt 910" boundary on migration gating is a clean out-of-scope signal rather than a hidden coupling.

### [PASS] Cutover failure handling is appropriately concrete for the I/O paths it owns

For the production-side I/O (the cutover), the slice is more concrete than for the publish side: D7 specifies an explicit rollback ("stop the daemons, restore the previous unit files, restart"), the Risks section names the service-user path assumption as something to verify on the host rather than assume, and success criterion 7 requires observable heartbeat/`acquisition_state` advancement — not merely process started. This is the kind of failure handling the review criteria look for; the gap is on the publish side (see separate NOTE), not the cutover side.
