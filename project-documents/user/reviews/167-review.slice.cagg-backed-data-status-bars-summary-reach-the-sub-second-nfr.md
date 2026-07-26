---
docType: review
layer: project
reviewType: slice
slice: cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/167-slice.cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr.md
aiModel: claude-fable-5
status: complete
dateCreated: 20260726
dateUpdated: 20260726
findings:
  - id: F001
    severity: concern
    category: architecture-drift
    summary: "Architecture's `data_status` spec contradicted without amendment"
    location: project-documents/user/slices/167-slice.cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr.md:25-29
  - id: F002
    severity: concern
    category: under-specification
    summary: "Freshness guard placement doesn't cover all consumers the slice itself enumerates"
    location: project-documents/user/slices/167-slice.cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr.md:252-259
  - id: F003
    severity: concern
    category: internal-consistency
    summary: "Success criterion 2 conflicts with D3's bucket-truncation bound"
    location: project-documents/user/slices/167-slice.cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr.md:384-386
  - id: F004
    severity: note
    category: accuracy
    summary: "D3a's daily-offset example cites caggs `bars_summary` won't read"
    location: project-documents/user/slices/167-slice.cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr.md:271-277
  - id: F005
    severity: pass
    category: nfr
    summary: "NFR restated with specific target and regression coverage"
    location: project-documents/user/slices/167-slice.cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr.md:383-392
  - id: F006
    severity: pass
    category: error-handling
    summary: "Failure modes for the new read path enumerated with explicit handling"
    location: project-documents/user/slices/167-slice.cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr.md:260-293
  - id: F007
    severity: pass
    category: scope
    summary: "Scope discipline and dependency direction correct"
    location: project-documents/user/slices/167-slice.cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr.md:96-145
---

# Review: slice — slice 167

**Verdict:** CONCERNS
**Model:** claude-fable-5

## Findings

### [CONCERN] Architecture's `data_status` spec contradicted without amendment

The architecture defines `bars_summary` fields as `MIN(time)`/`MAX(time)`/`COUNT(*)` **from the data table** (140-arch §One status view) and states "A view, not a table. Always consistent with the underlying data." This slice changes the source to continuous aggregates, making the view lag-bounded (D3) and bucket-truncated — the view is no longer "always consistent with the underlying data," it is consistent-within-a-documented-and-asserted staleness bound. That is a reasonable trade and the slice handles it well, but it changes an explicit architectural statement. The architecture doc has an established amendment convention (the 2026-07-20 `mt data caggs` amendment for slices 154/163); this slice should include an analogous 140-arch amendment updating the `data_status` spec and the "always consistent" claim, so the arch doesn't silently describe a view that no longer exists. No amendment is planned anywhere in the migration plan or success criteria.

### [CONCERN] Freshness guard placement doesn't cover all consumers the slice itself enumerates

D3a says "`bars_summary` calls `assert_cagg_fresh(conn, view_name)`" — but `bars_summary` is a SQL CTE inside the view, and `assert_cagg_fresh` (per slice 168, verified) is a Python helper in a shared maintenance module wired at *reader call sites*. The guard therefore cannot live "in" `bars_summary`; it must live in each Python read path, and the view itself remains unguarded at the SQL level. This matters because D2 explicitly enumerates consumers beyond the CLI: `migrate_cold_start.py`'s verification, "any external reader," and — per the interfaces section — slice 182's serving API reading "the same view." The slice's own inherited rule is "167 must never ship a second unguarded consumer," yet it commits to adding "only the `bars_summary` call site." Where the guard actually sits (which Python function), and whether the 182 API path and direct-SQL readers are guarded, gated, or explicitly accepted as unguarded, is unspecified. The design should name the exact call-site module(s) and state the contract slice 182 must follow (e.g., 182 must call the helper, or must read via a guarded accessor) — otherwise the 163 failure mode is reintroduced on the API surface.

### [CONCERN] Success criterion 2 conflicts with D3's bucket-truncation bound

Criterion 2 requires output "**identical** to the current raw-scan version for all settled history, and differs only within the documented cagg-lag bound for the trailing edge." But D3 establishes that `first_bar_ts`/`last_bar_ts` are truncated to the 4-hour bucket start for **all** history, settled included — a permanent value delta versus raw `MIN(time)`/`MAX(time)`, not a trailing-edge lag effect. A row-by-row equivalence check as written (migration plan "Behavior verification" and verification walkthrough step 3) will fail on every symbol's timestamps. The criterion needs to be restated as identical-modulo-both-documented-bounds (truncation everywhere, lag at the trailing edge), or the comparison defined as date-normalized. Relatedly, the "contract preserved" claim for interface 182 rests only on the CLI's date-only `_fmt_date` rendering; the serving API may expose the timestamps at full precision, where the up-to-4-hour coarsening is visible. The 182 impact should be assessed against that surface, not the CLI formatter.

### [NOTE] D3a's daily-offset example cites caggs `bars_summary` won't read

D3a justifies the absolute-ceiling threshold by noting `bars_summary` "also reads the **daily** caggs, whose offsets run to 21/90/**270** days." Per D1, the daily branch reads the new `daily_coverage` cagg (offset not yet chosen, per D4) — not the existing weekly/monthly/quarterly caggs that carry those offsets. The argument for `min(start_offset, ceiling)` remains valid (and is already codified in 140-arch's `MAX_COVERAGE_SOURCE_STALENESS`), but the supporting example misattributes which caggs are in this slice's read path.

### [PASS] NFR restated with specific target and regression coverage

The 140-arch NFR ("View latency stays sub-second at full-universe scope") is restated with the specific target, a measured baseline (7.8 s vs sub-second), and a CI-gated load test (D5, criterion 6) resolving slice 166's recorded deferral. This fully satisfies the NFR-restatement requirement.

### [PASS] Failure modes for the new read path enumerated with explicit handling

D3a enumerates four independent stall/failure signals with what each catches, an explicit threshold formula matching 140-arch's `MAX_COVERAGE_SOURCE_STALENESS` rationale, measured probe cost, an on-trip behavior (surface, never silently report stale as current), and a deliberate non-remediation decision with reasoning. Success criterion 7 requires proving the guard fires by inducing staleness. Probe-timeout degradation (`PROBE_FAILED` refusal) is covered in the arch constants that 168 added. This is the standard the criteria ask for — nothing is "TBD" on the failure-handling strategy itself (the guard *placement* gap is filed separately above).

### [PASS] Scope discipline and dependency direction correct

The discovered ~79% cagg under-materialization — a live production issue found during design — is documented but its repair is correctly folded into slice 163 rather than absorbed here, with the PM decision and the pay-materialization-once rationale recorded. Dependencies [166, 163, 168] are declared with the *kind* of dependency distinguished (data vs. design vs. shared artifact), and the shared helper is consumed, not reimplemented — dependency direction matches slice 168's stated sequencing. Constants centralized per the arch's Constants section and slice-166 pattern. The frontmatter `parent` pointing at the slice plan is per convention (not an error).
