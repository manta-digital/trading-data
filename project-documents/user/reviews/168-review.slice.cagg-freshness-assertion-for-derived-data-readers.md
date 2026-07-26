---
docType: review
layer: project
reviewType: slice
slice: cagg-freshness-assertion-for-derived-data-readers
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/168-slice.cagg-freshness-assertion-for-derived-data-readers.md
aiModel: claude-fable-5
status: complete
dateCreated: 20260726
dateUpdated: 20260726
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "Probe failure modes not enumerated — helper behavior on indeterminate freshness is unspecified"
    location: project-documents/user/slices/168-slice.cagg-freshness-assertion-for-derived-data-readers.md:97-107
  - id: F002
    severity: concern
    category: nfr-alignment
    summary: "167's sub-second NFR is not restated, and the measured probe cost consumes its entire budget"
    location: project-documents/user/slices/168-slice.cagg-freshness-assertion-for-derived-data-readers.md:150-153
  - id: F003
    severity: note
    category: under-specification
    summary: "`MAX_COVERAGE_SOURCE_STALENESS` default value left approximate"
    location: project-documents/user/slices/168-slice.cagg-freshness-assertion-for-derived-data-readers.md:89-95
  - id: F004
    severity: note
    category: consistency
    summary: "167 fallback clause is in mild tension with the plan's hard dependency"
    location: project-documents/user/slices/168-slice.cagg-freshness-assertion-for-derived-data-readers.md:178-181
  - id: F005
    severity: pass
    category: alignment
    summary: "Fail-safe design matches the architecture's honesty principles"
    location: project-documents/user/slices/168-slice.cagg-freshness-assertion-for-derived-data-readers.md:97-114
  - id: F006
    severity: pass
    category: alignment
    summary: "Plan entry, scope, and dependency directions are consistent"
    location: project-documents/user/slices/168-slice.cagg-freshness-assertion-for-derived-data-readers.md:171-181
resolution:
  status: addressed
  dateResolved: 20260726
  notes: "F001-F004 all addressed in the slice design; F002 resolved by PM decision (TTL verdict cache). See Resolution section."
---

# Review: slice — slice 168

**Verdict:** CONCERNS
**Model:** claude-fable-5

## Findings

### [CONCERN] Probe failure modes not enumerated — helper behavior on indeterminate freshness is unspecified

D3 fully specifies the **staleness-detected** path (log ERROR, return `None`, caller skips seeding — verified: `build_minute_coverage_index` already implements the `None`-on-failure contract). But the helper introduces three new I/O paths — the catalog read of `timescaledb_information.jobs` + `job_stats`, and two `max()` probes — and the design says nothing about what happens when those paths themselves fail:

- Catalog read returns **no row** for the view (a cagg with no refresh policy, or a view name not in `GRANULARITY_SOURCE` / not a cagg at all) — is that a trip, an exception, or a pass?
- Probe query timeout or hang. The raw-edge `max()` probe runs against a compressed hypertable — the exact query class behind the 2026-07-20 prod incident — yet no `statement_timeout` discipline is stated for the probes.
- Connection/operational error mid-probe.

The fail-safe direction is almost certainly "indeterminate ⇒ refuse" (consistent with D3), but the architecture's standard here is explicit specification ("nothing hand-waved" — cf. the gap-function section of 140-arch), and the review bar requires each new I/O path's failure modes enumerated with an explicit handling strategy, not implied. One sentence per case in D3 closes this.

### [CONCERN] 167's sub-second NFR is not restated, and the measured probe cost consumes its entire budget

The design budgets probe cost (~0.19 s cagg + ~0.75 s raw ≈ ~1 s) solely against the daemon's once-per-cycle ~23 s index build (D5, success criterion 5) — fine for the first consumer. But success criterion 6 and the Interfaces section explicitly design the helper for slice 167's `bars_summary` to consume "as-is," and that path carries the parent architecture's NFR: "View latency stays sub-second at full-universe scope" (140-arch.data-quality-operations.md:150), the very NFR slice 167 exists to reach. ~1 s of synchronous probes per read exceeds that budget by itself. The slice neither restates the consumer-path NFR with its target (required when a slice touches an NFR-bearing path) nor addresses how the helper stays inside it (verdict caching/TTL, once-per-N-reads amortization, or an explicit statement that per-read budgeting is 167's problem with the constraint named). As written, criterion 6 ("167 consumes it as-is") and 167's success criterion 6 (CI-gated < 1 s load test) are on a collision course that neither document resolves.

### [NOTE] `MAX_COVERAGE_SOURCE_STALENESS` default value left approximate

D2 correctly centralizes the ceiling in the constants module (matching the arch's Constants convention), but pins it only as "~1 day for the acquisition path." The arch's convention is exact defaults with rationale (`MAX_GAP_STALENESS = 5 minutes`, `LATE_BAR_GRACE_PERIOD = 30 minutes`), and this value is load-bearing — success criterion 3's regression test depends on it. The design should pin the exact value and state whether one ceiling serves both the acquisition path and 167's status path, and the 140-arch Constants section should be amended when it lands.

### [NOTE] 167 fallback clause is in mild tension with the plan's hard dependency

The Interfaces section says "If this slice has not delivered when 167 starts, 167 must build the helper itself." The slice plan, however, lists 168 as a hard dependency of 167 (`Dependencies: [166, 163, 168]`), under which that scenario cannot occur. The clause is mirrored verbatim in 167's D3a, so the two designs are coordinated — but the plan and the designs express different sequencing contracts (hard dep vs. soft-with-fallback). Harmless as belt-and-braces; worth one line stating which is authoritative.

### [PASS] Fail-safe design matches the architecture's honesty principles

D3 (refuse, never fall back — explicitly rejecting the full-window seed that would reintroduce the 162 failure) and D4 (detect-and-refuse; remediation stays with runbook R2) align precisely with the parent architecture: "Nothing in the system silently substitutes for missing vendor data," honest gaps preferred, and the deliberate design-out of an auto-recovery coordinator (140-arch "Out of scope"). Keeping heavy writes out of the read path respects layer responsibilities.

### [PASS] Plan entry, scope, and dependency directions are consistent

Slice-plan item 28 matches the design on every substantive point (four OR'd signals, `min(start_offset, ceiling)` threshold, the 270-day false negative, first consumer, refusal semantics, probe costs). Index 168 sits in the 140 initiative band with a proper plan entry. `dependencies: [163]` / `interfaces: [162, 167]` match the plan and 167's frontmatter; 167's D3a expects exactly the `assert_cagg_fresh(conn, view_name)` signature this slice defines. The shared-maintenance-module placement and `GRANULARITY_SOURCE` resolution follow 163's shipped precedent (both exist in the tree). Success criteria demand induced staleness, not mocks — matching the initiative's standing lesson that this failure class is invisible to indirect checks.

## Resolution (20260726)

All four actionable findings addressed in the slice design before implementation
started. No finding was waived.

**F001 — probe failure modes.** D3 gains an "indeterminate freshness is treated as
stale" subsection with a per-mode table: no job row for the view → trip (a cagg with
no refresh policy is the strongest form of the incident, not an exemption); view name
not a cagg / not in `GRANULARITY_SOURCE` → `ValueError` (caller bug, must not be
absorbed into a refusal); probe timeout / connection loss / any other `psycopg.Error`
→ trip, logged via `logger.exception`, never propagated into the reader's error path.
Explicit `statement_timeout` on every probe is now stated. Noted for the record: the
raw-edge probe is a bounded `max(time)` index scan, not an expression aggregate over
compressed chunks, so it is not the 2026-07-20 incident query shape — the timeout
discipline applies regardless.

**F002 — NFR collision.** The finding is correct and was the substantive one: ~1 s of
synchronous probes is free against the daemon's ~23 s cycle but is the entire budget
on 167's CI-gated sub-second read path. **PM decision: cache the verdict with a short
TTL.** New D6 memoizes per view name for `CAGG_FRESHNESS_CACHE_TTL` (60 s) — two
orders of magnitude below `MAX_COVERAGE_SOURCE_STALENESS`, so a cached verdict cannot
mask a lag the uncached check would catch; stale verdicts are cached on the same terms
as fresh ones, so the cache never converts a refusal into a pass. The daemon's cycle
exceeds the TTL and so always probes; `bars_summary` amortizes to ~0 per read across a
full-universe view. Cache state is process-local and keyed by view name only, so it is
explicitly not for maintenance decisions — 163's `preflight()` remains the uncached,
always-probing maintenance guard. New success criterion 8 pins the cache by query
count rather than timing, covers the cached-stale-still-refuses direction, and requires
expiry to re-probe. 167's D3a updated to note it needs no amortization scheme of its
own.

**F003 — ceiling value.** Pinned exactly: `MAX_COVERAGE_SOURCE_STALENESS =
timedelta(days=1)`, matching the existing `MINUTE_STALENESS_THRESHOLD` convention and a
full refresh cycle above every minute policy's `start_offset`. One ceiling serves both
the acquisition and 167 status paths — a derived read older than a full trading day is
stale for either purpose, and a second per-consumer ceiling would be a tuning knob with
no distinct failure it catches. The 140-arch Constants section is amended when this
lands.

**F004 — sequencing contract.** The slice plan is now stated as authoritative: 167's
`dependencies: [166, 163, 168]` is a hard dependency, so the fallback scenario cannot
occur. The "167 builds the helper itself" clause is **removed** from 168's Interfaces
section and from all three places it appeared in 167 (D3a preamble, D3a decision body,
cross-slice shared-artifact entry). Keeping it invited a second implementation of a
guard whose entire value is being shared.

Plan entry 28 in `architecture/140-slices.data-quality-operations.md` updated with the
TTL cache, the exact ceiling, and the indeterminate-freshness handling so the plan and
design stay in agreement.
