---
docType: review
layer: project
reviewType: slice
slice: archive-generalisation
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: PASS
verdictSource: stated
sourceDocument: project-documents/user/slices/300-slice.archive-generalisation.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261028
dateUpdated: 20261028
reviewedSha: 0d3fa763e65bc5f994d1463f40e238c5e8842bc2
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 99809
cachedTokens: 11220
cacheWriteTokens: 88585
completionTokens: 3096
durationSeconds: 31.2
costUsd: 0.387179
costBasis: provider
runId: run-20261028-p4-fde8dd6d
squadronVersion: 0.24.6
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Family-scoped supersession and projector registry match the architecture"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#D1 — Schema families, from one ordered table"
  - id: F002
    severity: pass
    category: architecture-alignment
    summary: "Plan-inclusion marker and marginal guard match the \"Cost accounting\" decision"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#D6 — One inclusion table, total over schemas"
  - id: F003
    severity: pass
    category: error-handling
    summary: "Failure modes and safe defaults are explicit"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#D8 — The guard counts marginal spend"
  - id: F004
    severity: note
    category: consistency
    summary: "\"Errs short\" in D7 is overclaimed relative to the Risk section"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#D7 — A request is included only if every day of it is"
  - id: F005
    severity: note
    category: scope
    summary: "Definitions stay outside the registry, a bounded deviation from \"one registry\""
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#D3 — Definitions stay projected by the acquisition pass"
  - id: F006
    severity: note
    category: proportionality
    summary: "Proportionality is acceptable, with one trim available"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#Migration Plan"
---

# Review: slice — slice 300

**Verdict:** PASS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Family-scoped supersession and projector registry match the architecture

The architecture's "Generalising the archive beyond tick tiers" section asks for three things:
- Schema families.
- Supersession within a family only.
- A projector chosen per schema from one registry.

D1 and D2 deliver all three. `supersedes_sql` ANDs a same-family test with a rank test. `INGEST_PROJECTORS` is keyed by schema, not by family, so `ohlcv-1s` and `ohlcv-1m` can later use different writers. The bundle rule stays a tick-tier rule (D4), as the architecture says. `TICK_TIERS` remains the single statement of tier order, which avoids a second source and a circular import.

### [PASS] Plan-inclusion marker and marginal guard match the "Cost accounting" decision

The architecture requires a per-request marker set from one table of schema to included history, kept in configuration. It also requires the guard to count marginal spend only, with list prices kept on the row and invoice reconciliation limited to usage-billed rows. D5 to D9 cover each of these.

The table sits in `config/`, and the rule over it sits in `data/tick/`, so dependencies point from data to config. The slice also closes a gap the architecture names: unheld provider jobs are classified from `ts_received`, so they cannot slip past the sum. The marker is computed once on the plan item, so the guard and the stored row cannot disagree.

### [PASS] Failure modes and safe defaults are explicit

- **Absent `ts_received`:** this keeps today's behaviour. The job listing fails and the pass refuses before any spend.
- **Unknown plan value:** settings validation fails, and the preflight refuses.
- **Unset plan:** every request counts in full, which is today's behaviour.
- **Pending tick migration:** the preflight already refuses to run.
- **Writer that forgets the new column:** the dropped default means it fails on `NOT NULL`.
- **Projector or transaction errors:** the existing handling is unchanged.

The slice adds no new I/O or message path, so this level of enumeration is proportionate. The architecture states no NFR for these paths, and the slice correctly adds none.

### [NOTE] "Errs short" in D7 is overclaimed relative to the Risk section

D7 says a short window means an overcount "only refuses, never overspends". The Risk Assessment concedes that if Databento's window is narrower than 365 days, an inclusion claim could hide a charge. 365 days is not provably the shortest plausible reading of "last 12 months", for example if Databento measures from a billing anchor. No action is needed before implementation, because the plan is unset until a subscription starts. The D7 wording should match the Risk wording. The check against the first in-plan invoice should be recorded in the slice that first runs with a plan, as the mitigation already says.

### [NOTE] Definitions stay outside the registry, a bounded deviation from "one registry"

The architecture says ingest selects a projector per schema from one registry. `definition` is still projected by `project_definitions` in the acquisition pass. D3 justifies this: moving it would change when definitions land relative to tier ingest, which is a behaviour change this slice is meant not to make. The deviation is small and recorded. 301 inherits the follow-up and should confirm it still holds once full-range definitions are bought.

### [NOTE] Proportionality is acceptable, with one trim available

The slice is large (relative effort 4/5) but each part traces to an architecture requirement.
- **One-member `DatabentoPlan` enum:** this is a small abstraction. It is justified by validating the setting and by keying the per-plan table.
- **Migration Plan table and "Consumers deliberately left" list:** these partly restate the Technical Scope and Component Structure sections. They could be shortened at implementation, but they are not misleading.

Nothing in the plan entry has been dropped or deferred. Status, coverage, health and accounting are rightly left for the slices that add a tier.

### Run Digest

- Response length: 4939 chars
- Response is newline-free: no
- Tool calls made: 2
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 3
- Tokens — prompt / cached / cache write / completion / reasoning: 99809 / 11220 / 88585 / 3096 / not computed
- Duration: 31.2 s
- Cost: $0.39 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
