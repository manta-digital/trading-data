---
docType: review
layer: project
reviewType: slice
slice: mt-update-self-update-command
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/slices/909-slice.mt-update-self-update-command.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260802
dateUpdated: 20260802
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Slice aligns with discoverable CLI and shared `--json` output convention"
    location: 909-slice.mt-update-self-update-command.md#Architecture
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Single-source-of-truth via enums and centralized constants"
    location: 909-slice.mt-update-self-update-command.md#D2 — Registry endpoint and failure contract
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Sync-first architecture preserved"
    location: 909-slice.mt-update-self-update-command.md#D5 — Auto-upgrade only the blessed path
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Comprehensive failure-mode enumeration across the data flow"
    location: 909-slice.mt-update-self-update-command.md#Data Flow
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Uses `importlib.metadata.version` per architecture's version management pattern"
    location: 909-slice.mt-update-self-update-command.md#D3 — `packaging.version`, declared explicitly
  - id: F006
    severity: concern
    category: error-handling
    summary: "Upgrade subprocess has no explicit timeout — could hang indefinitely"
    location: 909-slice.mt-update-self-update-command.md#D5 — Auto-upgrade only the blessed path
  - id: F007
    severity: note
    category: dependencies
    summary: "Adds `packaging` as a new direct dependency"
    location: 909-slice.mt-update-self-update-command.md#D3 — `packaging.version`, declared explicitly
  - id: F008
    severity: note
    category: documentation
    summary: "Reference implementation is `cf update` (context-forge) rather than Squadron"
    location: 909-slice.mt-update-self-update-command.md#Overview
  - id: F009
    severity: note
    category: uncategorized
    summary: "Distribution name evolution is consistent with slice 908"
    location: 909-slice.mt-update-self-update-command.md#D3 — `packaging.version`, declared explicitly
---

# Review: slice — slice 909

**Verdict:** PASS
**Model:** minimax/minimax-m3

## Findings

### [PASS] Slice aligns with discoverable CLI and shared `--json` output convention

The architecture states "All commands support `--json` for machine consumption" and calls for discoverable commands reachable via `mt --help`. The slice adds a top-level `mt update` registered alongside `serve` in `cli/app.py`, and `--json` (D7) is implemented as a pure query (no prompt, no subprocess, no DB) with a documented JSON shape. Pure-query mode emits the four documented keys and degrades gracefully on editable-source and registry failure — consistent with the architecture's CLI principles.

### [PASS] Single-source-of-truth via enums and centralized constants

InstallMethod is implemented as a `StrEnum` (D4) and all comparison values — `PYPI_JSON_URL_TEMPLATE`, `REGISTRY_TIMEOUT`, `UPDATE_MIGRATE_PROBE_TIMEOUT`, install-method strings, and upgrade argv — are placed in `constants.py` per the architecture's "No magic strings" principle and the single-definition rule. This is a faithful application of the architecture's dispatch convention.

### [PASS] Sync-first architecture preserved

The architecture states "No part of the application expects a pre-existing event loop." The slice uses sync `httpx` (already direct) and `subprocess.run` with stdio inherited and no shell — entirely consistent with sync-first. D9's startup-cost note (no network/DB/heavy imports at module level) reinforces the constraint.

### [PASS] Comprehensive failure-mode enumeration across the data flow

Each branch in the data flow has an explicit failure strategy, not "TBD": registry unreachable → exit 1; editable/source → refuse pre-network; non-TTY without `--yes` → report-only exit 0; declined prompt → exit 0; `uv` missing from PATH → degrade to printed command; upgrade subprocess non-zero → report with output and exit non-zero; migration probe (timeout, non-zero, unparseable, disconnected) → generic pointer line that cannot change the update's exit code. D2 enumerates the specific exception classes (`httpx.HTTPError`, `ValueError`, `KeyError`, `TypeError`) for the registry call. D8 consolidates the exit-code contract. This is the level of explicit failure handling the architecture's "Explicit failure" principle calls for.

### [PASS] Uses `importlib.metadata.version` per architecture's version management pattern

The architecture specifies `importlib.metadata.version(...)` as the single source of truth, falling back to `"dev"` when metadata is unavailable. The slice uses the same pattern via `DISTRIBUTION_NAME` (from slice 908) and notes that `PackageNotFoundError` is already caught earlier by the editable/source detection in D4, so the comparison is guaranteed a real version string by the time it runs. Using the constant rather than a string literal is even more disciplined than the architecture's example.

### [CONCERN] Upgrade subprocess has no explicit timeout — could hang indefinitely

The slice bounds the registry call (10 s) and the post-upgrade migration probe (30 s), but the `uv tool install --upgrade` subprocess itself is run via `subprocess.run` with stdio inherited and **no timeout**. The upgrade involves a second network roundtrip to PyPI (download), a build/install step, and could legitimately hang on a slow mirror, a black-holed host, or a stalled build — none of which would surface as a non-zero exit. This is a real omission in the otherwise thorough failure-mode enumeration, and it conflicts with the architecture's "Explicit failure" principle: a silent hang is the opposite of explicit failure. Recommendation: specify an `UPGRADE_TIMEOUT` constant in `constants.py` (mirroring the `REGISTRY_TIMEOUT` / `UPDATE_MIGRATE_PROBE_TIMEOUT` pattern), pass it to `subprocess.run(timeout=...)`, and enumerate the `TimeoutExpired` handling in the failure contract (e.g., report timeout, suggest manual run, exit non-zero). Note that stdio is inherited for visibility, so the timeout needs to be sized to accommodate legitimate slow installs on large wheels.

### [NOTE] Adds `packaging` as a new direct dependency

The architecture's "Minimal new dependencies" principle was scoped to the foundation initiative ("Typer, Rich, and tomli_w are the only new dependencies this initiative introduces"), so adding `packaging` in this slice is within the architecture's intent. The rationale — correctness-critical PEP 440 comparison where transitive dependency status is fragile — is sound and documented. Worth a note rather than a concern because `packaging` is a small, ubiquitous library and the slice's dependency discipline (declared in `pyproject.toml`, not transitive) is consistent with the architecture's spirit of explicit, well-bounded dependency choices.

### [NOTE] Reference implementation is `cf update` (context-forge) rather than Squadron

The architecture's "Model on Squadron" principle names `~/source/repos/manta/squadron/` as the reference. The slice ports from `context-forge/packages/cli/src/commands/update.ts` (a different project in the manta ecosystem). Both are valid references for a proven implementation, and modeling on a known-good port is consistent with the spirit of "replicate the structural patterns." Worth a note because a future reader doing the kind of cross-architecture consistency check this review performs might wonder why the reference is different — a one-line acknowledgement in the Overview that `cf update` is the chosen production-proven reference (rather than a Squadron equivalent) would close the loop.

### [NOTE] Distribution name evolution is consistent with slice 908

The architecture document uses `"manta-trading"` as the package name; the slice uses `DISTRIBUTION_NAME = "manta-trading-data"` (introduced by slice 908). The slice correctly defers to the canonical constant rather than a string literal, which is more disciplined than the architecture's example. No action required — this is forward evolution handled cleanly via the single-source-of-truth pattern. Flagging as a note only because it surfaces the rename for any reviewer comparing the two documents side-by-side.
