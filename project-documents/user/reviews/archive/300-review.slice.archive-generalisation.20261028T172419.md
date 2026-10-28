---
docType: review
layer: project
reviewType: slice
slice: archive-generalisation
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/300-slice.archive-generalisation.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261028
dateUpdated: 20261028
reviewedSha: 7ed63e33ab0faa2ee1b23b443e89116d9c485a5f
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 3
turns: 4
promptTokens: 99375
cachedTokens: 8842
cacheWriteTokens: 90529
completionTokens: 4780
durationSeconds: 45.0
costUsd: 0.411557
costBasis: provider
runId: run-20261028-p4-fde8dd6d
squadronVersion: 0.24.6
findings:
  - id: F001
    severity: pass
    category: alignment
    summary: "Family, registry and marker match the architecture's position"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#Technical Decisions"
  - id: F002
    severity: concern
    category: spec-consistency
    summary: "Criterion 3 contradicts D3 for `definition` units"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#Success Criteria (Functional Requirements, item 3) and D3"
  - id: F003
    severity: concern
    category: error-handling
    summary: "No failure handling for unheld jobs that cannot be classified"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#D8 — The guard counts marginal spend; list prices stay visible"
  - id: F004
    severity: concern
    category: risk
    summary: "\"A wrong plan setting can only make the guard stricter\" is false"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#Value"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Parity script and extra list-total plumbing may be heavier than needed"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#D10 and D8"
  - id: F006
    severity: note
    category: alignment
    summary: "Inclusion table is in code, the architecture says configuration"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#State Management"
  - id: F007
    severity: pass
    category: testing
    summary: "Family-scoped rules are tested, including against a future family"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#Mitigation Strategies"
  - id: F008
    severity: pass
    category: integration
    summary: "Integration points with 301–306 are specific and match the architecture"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#Integration Points"
---

# Review: slice — slice 300

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Family, registry and marker match the architecture's position

- **Supersession (D1):** it applies within a family only, so a bar unit can never supersede a `tbbo` unit.
- **Projector registry (D2):** it is keyed by schema, which fits the architecture's "projector per schema from one registry". `tick_trade` and the ledger move into the tick-tier projector, so the worker becomes schema-agnostic.
- **Bundle rule (D4):** `_companion_wants` and the estimate bundle stay tick-tier rules, as the architecture requires.
- **Namespace:** the `tick` track, `MT_TICK_*` and `mt data tick` keep their names.
- **Deferred families:** declaring only `TICK_TIER` and `DEFINITION` now, and leaving `BAR` and `STATISTICS` to 301 and 302, is proportionate.
- **Slice plan scope:** I found nothing the slice plan assigns to 300 that this design drops.

### [CONCERN] Criterion 3 contradicts D3 for `definition` units

- **D3:** `definition` units are projected in the acquisition pass and are deliberately not in `INGEST_PROJECTORS`.
- **Criterion 3:** it says any verified unit whose schema has no projector is reported `not_selectable` with "no ingest projector for schema X".
- **The conflict:** read literally, every verified `definition` unit would now appear as `not_selectable` noise. That changes operator-visible output and could fail the parity run (criterion 1).
- **Fix:** scope criterion 3 to schemas that are neither in the registry nor projected by the acquisition pass. Alternatively, state that `definition` is excluded from this report. Then name the test that proves it.

### [CONCERN] No failure handling for unheld jobs that cannot be classified

- **D8 and the purchase flow:** both classify each unheld provider job from `BatchJob.request` and `ts_received`.
- **Not specified:**
  - what happens when the provider job lacks `ts_received`, for example a job still queued or processing;
  - what happens when its request has an unknown dataset or schema, or an unparseable range.
- **Unclassified jobs:** `included_plan` is total over `TickSchema` and returns `None` for unknown inputs. A job with an unknown schema or dataset has no table entry, and the design never says whether that raises or counts as usage-billed.
- **Fix:** state that an unclassifiable job counts at its estimate, which is the stricter side. Say whether the guard reports it. Add a unit test for it in `evaluate_spend`.

### [CONCERN] "A wrong plan setting can only make the guard stricter" is false

- **Missing or unknown value:** this does fail on the strict side, because a missing plan counts everything and an unknown value is refused.
- **Wrong but valid value:** setting `MT_TICK_DATABENTO_PLAN=standard` without an active subscription is accepted. It zeroes the guard for every in-window `tbbo`, `trades` and `mbp-1` request, and for all `definition` requests of any age. The Risk section covers a wrong boundary, but not a wrongly configured plan.
- **Why it matters:** the guard exists to protect usage-billed spend. This slice makes it trust one environment variable.
- **Fix:** correct the claim in Value. Then either surface the active plan in the pass report and `mt data health`, so a stale setting is visible, or state this as an accepted risk with its owner. Do not add an approval gate.

### [CONCERN] Parity script and extra list-total plumbing may be heavier than needed

- **D10, `scripts/parity_300.py`:**
  - It adds two subcommands, a snapshot over five tables, a worktree at the base commit and a diff, to prove a refactor where the tick set is unchanged.
  - The existing tick tests plus the 226 harness's `rebuild` sequence and the 228 fingerprint already exist.
  - Consider a single snapshot-and-compare run reusing the existing fingerprint, instead of a new two-command tool with its own natural-key dumps. At minimum, scope the dump to the tables the refactor touches (`tick_archive_unit`, `tick_ingest_ledger`, `tick_trade`).
- **D8, list totals:**
  - The architecture requires only that list-price estimates "stay on the row for information".
  - The design also adds `planned_list_total` and `included_list_total` to `SpendVerdict` and a second trailing read.
  - It adds a new `--json` field and a new render line, "marginal $X (list $Y, $Z included in plan)".
  - Consider showing the included total alone, or deferring list totals, unless an operator need is named.
- **D9, estimate changes:** these are consistent with the guard but go beyond the architecture's text. Keep them only if they are needed for the guard and the estimate not to disagree, which is the stated reason.

### [NOTE] Inclusion table is in code, the architecture says configuration

- **Deviation:** the architecture's Cost accounting paragraph says the inclusion table is "kept in configuration". The slice puts it in code (`PLAN_INCLUDED_HISTORY` in `schema_families.py`) and calls it "configuration state".
- **Assessment:** this is a sound choice, because it is total-checked by a test and versioned with the pricing-page date. A reader should still not be surprised by it.
- **Placement:** `DatabentoPlan` and the pricing table live in `schema_families.py` next to the family table. `config/__init__.py` imports `DatabentoPlan` from the data layer. Consider placing the pricing table with `plan_inclusion.py` so the file does not hold two concerns.

### [PASS] Family-scoped rules are tested, including against a future family

The test-only extra family that asserts the family predicate appears in the selection and supersession SQL covers the risk that parity cannot catch (a missed `STORED_TIERS` use). The `INGEST_PROJECTORS` keys equal `STORED_TIERS` test, the totality and disjointness tests, and the end-of-slice grep make the "no change for 301" claim checkable.

### [PASS] Integration points with 301–306 are specific and match the architecture

The checklist for adding a schema is concrete: `TickSchema`, `FAMILY_SCHEMAS`, `INGEST_PROJECTORS`, `PLAN_INCLUDED_HISTORY` and a `CHECK`-widening migration. The `included_in_plan IS NULL` reading for the 303 invoice comparison matches the architecture's rule that reconciliation applies to usage-billed requests. Deferring splitting at the plan boundary (D7) is conservative and consistent with "overcount only refuses, never overspends".

### Run Digest

- Response length: 7833 chars
- Response is newline-free: no
- Tool calls made: 3
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 4
- Tokens — prompt / cached / cache write / completion / reasoning: 99375 / 8842 / 90529 / 4780 / not computed
- Duration: 45.0 s
- Cost: $0.41 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
