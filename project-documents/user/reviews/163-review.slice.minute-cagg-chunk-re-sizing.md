---
docType: review
layer: project
reviewType: slice
slice: minute-cagg-chunk-re-sizing
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/163-slice.minute-cagg-chunk-re-sizing.md
aiModel: claude-fable-5
status: complete
dateCreated: 20260720
dateUpdated: 20260720
findings:
  - id: F001
    severity: concern
    category: integration-consistency
    summary: "New `mt data cagg` group collides with existing `mt data caggs` group"
    location: project-documents/user/slices/163-slice.minute-cagg-chunk-re-sizing.md:188-198
  - id: F002
    severity: concern
    category: error-handling
    summary: "\"Per-window transaction boundaries\" claim is not achievable with `refresh_continuous_aggregate`; drop-vs-refresh crash window not enumerated"
    location: project-documents/user/slices/163-slice.minute-cagg-chunk-re-sizing.md:200-202
  - id: F003
    severity: concern
    category: availability
    summary: "\"Never-broken-intermediate-state\" claim overstated — each window has a transient zero-coverage serving gap"
    location: project-documents/user/slices/163-slice.minute-cagg-chunk-re-sizing.md:135-141
  - id: F004
    severity: note
    category: doc-drift
    summary: "140-arch \"Operator commands\" section not amended for the new command group"
    location: 140-arch.data-quality-operations.md#operator-commands
  - id: F005
    severity: note
    category: error-handling
    summary: "Query-discipline guards (statement_timeout) not stated for verify/repair's long-running prod queries"
    location: project-documents/user/slices/163-slice.minute-cagg-chunk-re-sizing.md:188-202
  - id: F006
    severity: pass
    category: alignment
    summary: "Scope expansion is authorized, recorded, and consistent with the parent plan"
    location: project-documents/user/slices/163-slice.minute-cagg-chunk-re-sizing.md:42-45
  - id: F007
    severity: pass
    category: nfr
    summary: "NFR restated with specific targets"
    location: project-documents/user/slices/163-slice.minute-cagg-chunk-re-sizing.md:275-293
  - id: F008
    severity: pass
    category: alignment
    summary: "Constants and failure-refusal discipline align with architecture and project rules"
    location: project-documents/user/slices/163-slice.minute-cagg-chunk-re-sizing.md:143-152
---

# Review: slice — slice 163

**Verdict:** CONCERNS
**Model:** claude-fable-5

## Findings

### [CONCERN] New `mt data cagg` group collides with existing `mt data caggs` group

D5 introduces `mt data cagg verify` / `mt data cagg repair` as a new command group, citing `mt data rechunk` as the pattern. But slice 154 (complete, per the 140 plan entry 14) already shipped a `mt data caggs {refresh,status}` group, and the plan's future-work notes reference `mt data caggs refresh` as an existing operator step. The design never mentions the existing group. Two top-level groups differing only by a trailing "s", both operating on the same four continuous aggregates, is exactly the fragile-label surface the project rules warn against and a DRY violation in the command taxonomy. The design should either place `verify`/`repair` under the existing `caggs` group or explicitly justify a separate group and rename to avoid the near-collision.

### [CONCERN] "Per-window transaction boundaries" claim is not achievable with `refresh_continuous_aggregate`; drop-vs-refresh crash window not enumerated

D5 states repair is "safe to kill mid-window (per-window transaction boundaries)". TimescaleDB's `refresh_continuous_aggregate` cannot run inside a transaction block, so the D1 sequence (`drop_chunks` → `refresh` → `compress_chunk`) cannot be a single transaction; the three steps commit independently. A crash or Ctrl-C between `drop_chunks` and refresh completion leaves that window's cagg region **empty** — and because `materialized_only = true`, consumers are served zero rows for it until the next repair invocation. The parity-based resume (D1) does heal this correctly on re-run, so the design is sound in outcome, but the stated mechanism is wrong and the specific failure mode (kill/crash after drop, before refresh commits) is not enumerated with its consequence and recovery path. Restate resumability as parity-derived (which it is) rather than transactional, and enumerate the drop-to-refresh gap explicitly.

### [CONCERN] "Never-broken-intermediate-state" claim overstated — each window has a transient zero-coverage serving gap

D1 rejects global TRUNCATE specifically because the cagg "would serve *nothing* until the multi-hour sweep completes" and claims the per-window approach "preserves 166's proven never-broken-intermediate-state property." But the chosen mechanism drops each window's chunks before refreshing them, so during each window's drop→refresh interval consumers see 0% coverage for that window — worse than the ~21% they see today. Slice 166's property held because raw restructuring used stage-to-temp under lock; this design (correctly, per its own D1 reasoning) does not stage. The per-window blast radius is small and bounded, which is a legitimately better availability story than TRUNCATE — but the design should state the bounded per-window gap honestly rather than claim the intermediate state is never broken, since 162's coverage queries and the 182 bars path read these caggs live during the multi-hour sweep.

### [NOTE] 140-arch "Operator commands" section not amended for the new command group

The architecture document enumerates five operator command groups (daemon, ca, status, refetch, audit); `mt data cagg[s]` verify/repair — positioned in this design as the *standing* detector/heal required after any raw restructuring, i.e. permanent operational surface, not one-off tooling — is absent. Slice 152 set the precedent of an "Architecture amendment" when a slice changes the arch's stated surface. A short amendment (or a follow-up commit to the arch doc) keeps the arch authoritative for question 3 of its Purpose ("are the stored prices correct?"), which `cagg verify` now partially answers at the aggregate level.

### [NOTE] Query-discipline guards (statement_timeout) not stated for verify/repair's long-running prod queries

The project's standing prod-query discipline (journal, 2026-07-20 incident) requires `statement_timeout` on all prod queries and warns that aggregates over compressed hypertables can decompress everything. The design's parity checks are argued cheap post-166 ("both now cheap"), which is credible, but the verify/repair paths issue many bounded raw `COUNT(*)`s and cagg `SUM(minute_count)`s against prod without stating the timeout/cancel discipline the pre-flight otherwise exemplifies. One sentence in D5 closes it.

### [PASS] Scope expansion is authorized, recorded, and consistent with the parent plan

The folded repair, mandatory compression (D3), verify/repair surface (D5), and 167-blocker status all appear verbatim in 140-slices entry 23 (including the 2026-07-20 urgent addition and the fold-here-to-avoid-paying-re-materialization-twice rationale), with effort already updated to 3/5 there. Dependencies [152, 166] and interfaces [162, 164, 167, 182] match the plan; 164 has a plan entry though no design doc yet, which is consistent with its not-started state. No scope creep beyond what the plan records.

### [PASS] NFR restated with specific targets

The slice touches the cagg serving path and restates concrete targets: single-symbol 4h cagg read ~2 s → sub-100 ms (Success Criterion 3, with before/after EXPLAIN required), full parity within the refresh-lag bound (Criterion 2), and footprint bounds (Criterion 4). The parent arch's sub-second `data_status` NFR is correctly assigned to slice 167, which this slice unblocks rather than implements.

### [PASS] Constants and failure-refusal discipline align with architecture and project rules

`MINUTE_CAGG_CHUNK_INTERVAL` and `compress_after` are defined once in `constants.py` and rendered into migrations (matching the arch's Constants pattern and the no-magic-defaults rule); job IDs are resolved from the catalog at runtime rather than hardcoded; pre-flight refuses rather than warns (166 precedent); cold-start correctness is proven from migrations alone (156's migration-list-as-truth principle). Dependency direction is correct throughout: derived data rebuilt from raw, never the reverse.

## Resolutions (2026-07-20, all findings verified before acting)

- **F001 — RESOLVED.** Verified: `mt data caggs {refresh, status}` exists (slice 154, `cli/commands/data.py:59-73`). D5 rewritten: `verify`/`repair` join the **existing** `caggs` group; relationship to `caggs refresh` stated (refresh = routine re-materialization wrapper; repair = restructuring sweep). All docs/memory renamed `mt data cagg` → `mt data caggs`.
- **F002 — RESOLVED.** Confirmed: `refresh_continuous_aggregate` cannot run in a transaction block. D5's "per-window transaction boundaries" claim replaced with parity-derived resumability; D1 gains an explicit **crash-window enumeration** (kill after drop → bounded zero-coverage window healed on re-run; kill before compress → policy backstop; kill mid-refresh → parity decides).
- **F003 — RESOLVED.** D1's rejected-TRUNCATE paragraph rewritten: availability property restated honestly as **bounded per-window zero-coverage gaps** (seconds-to-minutes each), not 166's never-broken-intermediate-state; live readers (162 coverage, 182 bars) named; off-hours guidance added.
- **F004 — RESOLVED.** 140-arch Operator commands amended with a `mt data caggs` subsection (slices 154 + 163, verify/repair, standing post-restructuring rule, Purpose-question-3 linkage).
- **F005 — RESOLVED.** D5 now requires `statement_timeout` on every prod query issued by verify/repair and backend cancellation on client interrupt, citing the 20260720 journal discipline.
