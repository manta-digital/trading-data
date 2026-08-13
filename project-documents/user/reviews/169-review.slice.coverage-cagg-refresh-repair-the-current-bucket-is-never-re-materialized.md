---
docType: review
layer: project
reviewType: slice
slice: coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized
project: trading-data
verdict: FAIL
sourceDocument: project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260813
dateUpdated: 20260813
reviewedSha: 8b5887f884168f35c32a431fa03c154018540fe8
dispositionDate: 20260813
dispositionStatus: all-findings-addressed-pending-re-review
findings:
  - id: F001
    severity: fail
    category: correctness
    summary: "Narrowing the bucket makes the generic freshness guard report permanent staleness; only the content-edge threshold is re-derived"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:175-192"
  - id: F002
    severity: fail
    category: migration-correctness
    summary: "`data_status` cannot be left untouched by 051 — it holds a hard dependency on the caggs being dropped"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:344-348"
  - id: F003
    severity: concern
    category: architecture-alignment
    summary: "The parent architecture is not amended, though the slice changes a constant and a guarantee the architecture states normatively"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:249-263"
  - id: F004
    severity: concern
    category: nfr
    summary: "The NFR is restated against the CTE, not against the full-universe view read the architecture specifies"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:383-384"
  - id: F005
    severity: concern
    category: error-handling
    summary: "The rebuild window's failure modes are not enumerated"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:328-342"
  - id: F006
    severity: concern
    category: documentation
    summary: "Width-dependent documentation beyond migration 046's description will silently go stale"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:260-263"
  - id: F007
    severity: note
    category: dependencies
    summary: "Slice 187 is a prerequisite, not just an interface"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:6-7"
  - id: F008
    severity: pass
    category: architecture-alignment
    summary: "Repair mechanism matches the architecture's complexity posture and the plan entry's stated options"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:67-98"
  - id: F009
    severity: pass
    category: operational-safety
    summary: "Rematerialization discipline correctly imports the 163 corruption lesson and the cagg-pause constraint"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:215-247"
---

# Review: slice — slice 169

**Verdict:** FAIL
**Model:** claude-opus-5

## Disposition (2026-08-13)

All seven findings **accepted and addressed** in the slice design. Two were
verified against the source before acceptance rather than taken on the
reviewer's word; both held, and one understated the problem.

| ID | Disposition | Where addressed |
|---|---|---|
| F001 | Accepted — verified | New **D3a**; success criterion 16; risks |
| F002 | Accepted — verified | Migration Plan steps ①②③; consumer lists; criterion 13 |
| F003 | Accepted | New **D6a**; criterion 15 |
| F004 | Accepted | Criterion 12 rewritten; walkthrough step 1 |
| F005 | Accepted | New section **The Rebuild Window**; risks row |
| F006 | Accepted — understated | **D5** expanded; criterion 14; walkthrough step 7a |
| F007 | Accepted | Frontmatter `dependencies`; new overview subsection |

**F001 verified:** `cagg_freshness.py:392` computes
`time_bucket(width, max(time))` on raw and `:481` caps the budget at
`min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)`. The mechanism is exactly as
described — today's `lag=0` holds only because the 365-day head bucket was
written once at creation, and narrowing removes that accident. Criteria 6 and 8
were unachievable as originally written.

**F002 verified:** migration 048 installs `data_status` via
`CREATE OR REPLACE VIEW` selecting from both caggs (`minute.py:297`,
`:401-407`), so the relation dependency is real and `DROP` would fail. The
original plan reasoned about column compatibility, which is irrelevant to a
`DROP`.

**F006 understated the defect.** The reviewer noted the doc comment would go
stale. It is **already false today**: `_data_status_doc_comment()`
(`minute.py:365-368`) renders CAGG LAG from schedule intervals alone — "2 hours
total" — a formula that never accounted for the open bucket, against a real
prod bound of months. So the fix is not a re-render with new constants; the
formula itself needs the bucket-width term. Raised from a documentation
refresh to a correctness fix.

**Note on staleness of this review.** It reviewed `8b5887f`. Two later commits
(`118d17e`, `019b6be`) added PM decisions to the *PM Decisions* section only and
did not touch any finding's subject matter, so every finding applied cleanly to
the current document.

## Findings

### [FAIL] Narrowing the bucket makes the generic freshness guard report permanent staleness; only the content-edge threshold is re-derived

D3 re-derives **only** `COVERAGE_CONTENT_STALENESS` (187 D6's content-edge check). It says nothing about slice 168's generic bucket-lag check, which the architecture makes mandatory for every reader of `data_status` (140-arch, "Every Python reader of `data_status` must go through that accessor").

That check compares `max(time_bucket)` on the cagg against `time_bucket(width, max(time))` on raw — both bucket *starts* — against a budget of `min(start_offset, MAX_COVERAGE_SOURCE_STALENESS) + end_offset` = **1 day 4 h** (140-arch#Constants, `MAX_COVERAGE_SOURCE_STALENESS = 1 day`). The architecture states the reason bucket width is not in that budget: "The raw edge is bucketed to the cagg's own grid before comparison, so the structural offset cancels exactly."

That cancellation holds only while the head bucket is materialized. This slice's central premise (D1: "It does *not* make the engine refresh an open bucket — nothing does") removes it. Failure scenario: after the 051/052 rebuild at 30 days, the newest *materialized* bucket is the most recently closed one, while the bucketed raw edge is the open bucket — so the generic lag is **exactly one bucket width, 30 days, permanently**, versus a 1 d 4 h threshold. `LAG_EXCEEDS_THRESHOLD` fires on every read of `minute_coverage` and `daily_coverage`, forever. This is not the status quo: today's 365-day cagg reports `lag=0` precisely because its head bucket *was* written once at creation, which is the false negative the slice describes. The slice therefore converts a silent false negative into a permanently-firing true positive — the exact "permanently-firing staleness signal is indistinguishable from a broken one" outcome D3 argues against, one paragraph after arguing against it.

Success Criteria 6 ("`check_coverage_freshness` reports fresh for both views") and 8 ("no longer report coverage staleness") cannot hold in steady state. The design must state what happens to the generic guard for these two views — a bucket-width term in the budget for open-bucket-tolerant caggs, a per-view threshold, or an explicit decision that the bucket-lag signal is suppressed for the coverage caggs in favour of the content-edge check — and that decision belongs in this doc, not in implementation.

### [FAIL] `data_status` cannot be left untouched by 051 — it holds a hard dependency on the caggs being dropped

The Migration Plan lists `data_status` under "**Consumers requiring no change:** ... 051 recreates the caggs with identical column names and types, so the view's `bars_summary` CTE binds unchanged." Column compatibility is not the constraint. Migration 048 installs `data_status` as a plain view whose `bars_summary` CTE selects from `minute_coverage` and `daily_coverage`, so PostgreSQL records a hard relation dependency.

Failure scenario: migration 051 issues `DROP MATERIALIZED VIEW minute_coverage` on prod; PostgreSQL raises `cannot drop ... because other objects depend on it` and the migration aborts partway through a two-view drop/recreate sequence that runs under `requires_autocommit` (no transactional rollback). If the implementer reaches for `CASCADE` instead — the obvious reflex, and the spelling used everywhere else in this migration module — `data_status` is silently dropped, and `mt data status`, `/api/v1/status`, and `/api/v1/health` fail against a missing relation until someone notices. The design must specify that 051 drops and re-installs `data_status` (re-running 048's branch-on-`to_regclass` definition and re-attaching its doc comment) as an ordered step, and Success Criterion 7 must verify the view exists and matches, not merely that its columns would have matched.

### [CONCERN] The parent architecture is not amended, though the slice changes a constant and a guarantee the architecture states normatively

140-arch#Constants specifies `COVERAGE_BUCKET_INTERVAL = 365 days` with a load-bearing rationale ("Sized so bars_summary groups ~15k rows..."), and 140-arch's slice-167 amendment states that `data_status` "is consistent with the underlying data only within a documented and asserted staleness bound." This slice changes the constant by ~12× and widens the asserted bound from 1 d 4 h to ~30 d 4 h — a change to what the architecture *promises the operator*, against its Purpose question 1 ("For symbol X, what data do we have, at what granularity, when?").

The doc has no step to amend 140-arch, and the architecture has an established amendment convention for exactly this (`*(Architecture amendment, 2026-07-26 — slice 167.)*`). Failure scenario: 169 ships, and 140-arch continues to state 365 days and a sub-1-day bound; the next slice designing against coverage reads the stale constant block and sizes its work wrong — the same "an operator reading the catalog would draw exactly the wrong conclusion" failure D1 rejects a custom job over. Add an explicit deliverable: amend 140-arch#Constants (`COVERAGE_BUCKET_INTERVAL`, the row-count rationale) and the slice-167 amendment's bounded-consistency paragraph. This should be tied to Open Question 2's resolution.

### [CONCERN] The NFR is restated against the CTE, not against the full-universe view read the architecture specifies

140-arch#Performance pattern states the NFR as "View latency stays sub-second at full-universe scope," and slice 167 measured it as `SELECT count(*) FROM data_status` (7.8 s → sub-second). Success Criterion 12 and Walkthrough step 1 both narrow this to `bars_summary` alone — a bare `SELECT symbol, MIN(...), MAX(...) FROM daily_coverage GROUP BY symbol`.

Failure scenario: at 30 days `daily_coverage` goes from ~780 k to ~9.5 M rows; the isolated `GROUP BY` clocks 0.4 s and passes criterion 12, while the full `data_status` read — which joins that CTE against `symbols`, `acquisition_state`, and the exchange-close CTE across ~57 k rows, and now materializes a 12× larger intermediate — lands at 1.5 s and regresses the actual architectural NFR undetected. Criterion 12 and Task B1's selection rule should measure the full-universe `data_status` read (the 167 load-tier shape), with the CTE timing as a diagnostic, not the gate.

### [CONCERN] The rebuild window's failure modes are not enumerated

The Migration Plan deliberately splits the drop/recreate (051) from the full-history materialization (an operational step, Task C) — a sound decision, but it opens an interval during which both coverage caggs exist and are empty or partially materialized. Nothing in the doc says what happens in that interval or how it ends badly.

Unenumerated: what `data_status`, `mt data status`, `/api/v1/status`, and `/api/v1/health` report while the caggs are empty (the guarded accessor's `cagg_max is None` path is maximal lag → stale, so every symbol reads as no-coverage — is that acceptable, and for how long?); whether the daemon must stay stopped for the whole interval or only during DDL; what happens if the multi-hour daily refresh hits a statement timeout, a client disconnect, or an operator Ctrl-C partway (the Risks table asserts "resumable" without saying what resumes it or how partial materialization is detected — `refresh_continuous_aggregate` over a full span is not chunk-committed the way the minute-fetch pattern is); and whether there is any path back to the prior state if the new width proves wrong after 051 has applied. "Run under the pausing runbook, outside a transaction, resumable" is the whole mitigation for the heaviest operation in the slice. Each of these needs a stated handling strategy before Phase 6.

### [CONCERN] Width-dependent documentation beyond migration 046's description will silently go stale

D5 claims "No literal width appears in SQL, tests, or docs" and lists migration 046's description as the one prose fix. At least two more artifacts carry the old numbers and are baked into already-applied migrations, so an existing database keeps the obsolete text: 046's description also states "so bars_summary groups **~15k rows**" (becomes ~1.6 M / ~9.5 M), and migration 048 attaches a `COMMENT ON VIEW data_status` that renders the cagg-lag bound from the constants — per 140-arch#Constants, "migration 048's COMMENT ON VIEW states these bounds on the view itself."

Failure scenario: 051/052 apply; an operator runs `\d+ data_status` on prod and reads a doc comment promising a lag bound of hours, while the actual guarantee is 30 d 4 h. The comment is the architecture's designated in-database statement of that bound, so it is exactly the artifact that must not lie. Add re-attaching the 048 doc comment (rendered from the new constants) to migration 052's scope, and include the row-count figure in the 046 prose fix.

### [NOTE] Slice 187 is a prerequisite, not just an interface

Frontmatter lists `dependencies: [167, 168, 170]` and `interfaces: [187]`. D3 makes 187 load-bearing in the dependency direction: the slice relies on 187 D6's content-edge check already existing in `status_coverage.py` as its truth-telling mechanism, and it *edits* `COVERAGE_CONTENT_STALENESS`, the constant 187 introduced. That is a consume-and-modify relationship, not an interface. Moving 187 into `dependencies` (it is shipped, so this costs nothing) would make the ordering constraint explicit for anyone reading the frontmatter alone.

### [PASS] Repair mechanism matches the architecture's complexity posture and the plan entry's stated options

Plan entry 29 offers two repairs — narrow the bucket, or add an explicit head-refresh job. D1 picks narrowing and rejects the custom job on grounds that align with the parent architecture rather than with taste: 140-arch#Out of scope already designs out a "Scheduled quality runner" ("Use cron if needed"), and D1 correctly notes that a cron on `.144` would be invisible to `timescaledb_information.jobs` and therefore to `mt data caggs status` — the operator-visibility principle the whole 140 architecture exists to serve. Ending with one refresh path and no lying catalog entry is the right call, and the honest framing of what narrowing does *not* fix (lines 99-102) is what makes D3's existence legible rather than looking like scope creep.

### [PASS] Rematerialization discipline correctly imports the 163 corruption lesson and the cagg-pause constraint

D4 gets the three things prior incidents on this system turned into hard rules: jobs resolved from the catalog by name rather than hardcoded IDs (with the 170 evidence for why), `minute_4hour_ohlcv` explicitly *not* paused (the re-seed loop), and full-history materialization issued explicitly because a 750-day policy window cannot heal 64 years. The choice of a new migration pair over editing applied 046/047 is likewise correct and correctly justified. This section is the strongest part of the design.
