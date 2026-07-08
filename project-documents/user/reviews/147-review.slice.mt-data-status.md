---
docType: review
layer: project
reviewType: slice
slice: mt-data-status
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/slices/147-slice.mt-data-status.md
aiModel: moonshotai/kimi-k2.6
status: complete
dateCreated: 20260504
dateUpdated: 20260504
findings:
  - id: F001
    severity: concern
    category: schema-alignment
    summary: "`data_gaps` schema mismatch: `first_attempt_ts` is not architecturally defined"
    location: 147-slice.mt-data-status.md#Decision B: Output shape
  - id: F002
    severity: concern
    category: error-handling
    summary: "Daemon idle-tick hook lacks hang/timeout failure mode handling"
    location: 147-slice.mt-data-status.md#Decision D: Auto-extension trigger points and gating
  - id: F003
    severity: note
    category: nfr-traceability
    summary: "Parent architecture view-latency NFR not formally restated"
    location: 147-slice.mt-data-status.md#Non-Functional Targets
  - id: F004
    severity: pass
    category: cli-design
    summary: "`mt data status` CLI surface aligns with architecture"
    location: 147-slice.mt-data-status.md#Decision A: Single command, not a sub-app
  - id: F005
    severity: pass
    category: data-integrity
    summary: "Health computation remains in SQL view"
    location: 147-slice.mt-data-status.md#Decision F: Health-state computation lives in the view, not Python
  - id: F006
    severity: pass
    category: schema-hygiene
    summary: "No `acquisition_state` sentinel row for daemon gating"
    location: 147-slice.mt-data-status.md#Decision D: Auto-extension trigger points and gating
---

# Review: slice — slice 147

**Verdict:** CONCERNS
**Model:** moonshotai/kimi-k2.6

## Findings

### [CONCERN] `data_gaps` schema mismatch: `first_attempt_ts` is not architecturally defined

The slice design assumes `data_gaps` contains a `first_attempt_ts` column, listing it in the `--symbol` gap table columns and the JSON schema (`first_attempt_ts`). The parent architecture (140-arch §"One control table") defines `data_gaps` with only `last_attempt_ts` and `attempt_count`; `first_attempt_ts` is absent from the schema and from the `update_data_gaps` algorithm. If the column does not exist (or was not added by a prerequisite slice), the detail view and JSON output cannot be implemented as specified. Verify whether slice 146 added this column; if not, either add it to the architecture/schema or remove it from the slice output.

### [CONCERN] Daemon idle-tick hook lacks hang/timeout failure mode handling

The slice introduces a new I/O path via `register_idle_hook` that invokes `maybe_extend_trading_sessions` inside the daemon's idle tick. While the slice enumerates failure modes for the status-command path (pool timeout, statement timeout, peer disconnect) and for logic errors inside `populate_trading_sessions`, it does not specify how the daemon runner protects against a hung or long-running hook (e.g., `populate_trading_sessions` hanging in Python calendar arithmetic or an unterminated DB transaction). The architecture requires explicit handling strategies for hang, timeout, and peer disconnect on every new I/O path. Add a timeout boundary (e.g., hook execution deadline or statement_timeout on the hook's connection) and specify whether the runner catches and logs hook exceptions or lets them propagate.

### [NOTE] Parent architecture view-latency NFR not formally restated

The parent architecture specifies that the `data_status` view "latency stays sub-second at full-universe scope" (140-arch §"Performance pattern"). The slice touches this path and references slice 142's sub-second measurement, but does not restate the view-query NFR as a specific target in its own Non-Functional Targets section. Consider adding an explicit line such as "`data_status` view query: <1s at ~57k rows (per arch Performance pattern)" to ensure traceability.

### [PASS] `mt data status` CLI surface aligns with architecture

The slice implements `mt data status` as a single Typer command with no subcommands, using `--symbol` and `--json` flags exactly as specified in the parent architecture (140-arch §"`mt data status [--symbol X]`"). The default scope (all symbols) and `--symbol` detail scope match the architectural specification.

### [PASS] Health computation remains in SQL view

The slice correctly delegates health classification (`OK / GAPS / STALE / FAILED`) to the `data_status` view per the parent architecture's health rules (140-arch §"Health rules"). The command layer only renders the view's output, preserving a single source of truth and avoiding drift between SQL and Python logic.

### [PASS] No `acquisition_state` sentinel row for daemon gating

The slice explicitly uses an in-process `_last_extend_at` module variable for the daemon's 24h gate, avoiding writes to `acquisition_state`. This preserves the architecture's definition of `acquisition_state` as strictly per-symbol run-state (140-arch §"Slimmed `acquisition_state`").
