---
docType: review
layer: project
reviewType: slice
slice: dedicated-test-database-cluster-stop-sharing-a-catalog-with-production
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/917-slice.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260819
dateUpdated: 20260819
reviewedSha: 08af82fcd2485b066809bed6db183768422260bc
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Aligns with explicit-failure principle and rejects silent skip"
    location: "project-documents/user/slices/917-slice.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md#technical-decisions"
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Respects existing interface contracts without rewriting them"
    location: "project-documents/user/slices/917-slice.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md#cross-slice-interfaces"
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Maintenance-band scope is corrective rather than additive"
    location: "project-documents/user/slices/917-slice.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md#out-of-scope"
  - id: F004
    severity: concern
    category: scope
    summary: "Cross-slice coupling to maintenance slices appears asymmetric"
    location: "project-documents/user/slices/917-slice.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md#cross-slice-interfaces"
  - id: F005
    severity: concern
    category: nfr
    summary: "Production host memory NFR is not restated against architecture targets"
    location: "project-documents/user/slices/917-slice.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md#technical-decisions"
  - id: F006
    severity: concern
    category: error-handling
    summary: "I/O and lifecycle failure modes are under-enumerated"
    location: "project-documents/user/slices/917-slice.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md#risks"
  - id: F007
    severity: concern
    category: error-handling
    summary: "Background-worker failure mode is acknowledged but not bounded"
    location: "project-documents/user/slices/917-slice.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md#technical-decisions"
  - id: F008
    severity: note
    category: uncategorized
    summary: "Verification walkthrough is self-marked as draft"
    location: "project-documents/user/slices/917-slice.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md#verification-walkthrough"
---

# Review: slice — slice 917

**Verdict:** CONCERNS
**Model:** minimax/minimax-m3

## Findings

### [PASS] Aligns with explicit-failure principle and rejects silent skip

The decision in D8 to convert the existing `pytest.skip("MT_TIMESCALE_TEST_URL not set")` into an error, plus the URL guard against pointing at the production cluster, directly mirrors the parent architecture's "Explicit failure" principle (no silent fallbacks) and the CLAUDE.md "Never use silent fallback values" rule. The refusal of stale `.env` values also matches the spirit of the parent doc's provider-credential design (missing required values raise immediately).

### [PASS] Respects existing interface contracts without rewriting them

D7 and D10 correctly honor the contracts that slice 915 (backup/restore's `with_replication` flag) and slice 913 (least-privilege roles) ship with. The maintenance-band rule that "the originating initiative's contracts are honored, not rewritten" is upheld — 915's opt-in stays as-is, and 913's guarantees are preserved by design (the constraint that forced 915's workaround no longer applies, so the workaround stays as a recorded historical fact, not a re-evaluation).

### [PASS] Maintenance-band scope is corrective rather than additive

The "Out of scope" section is rigorous: dev-host migration, CI runner design, 915's opt-in, 913's privileges, and pre-existing `test_cli_lists.py` failures are all explicitly excluded and routed to the correct owner. This is exactly the corrective-not-additive discipline the architecture calls out.

### [CONCERN] Cross-slice coupling to maintenance slices appears asymmetric

The slice reads 905, 907, 913, and 915 as `interfaces` in the frontmatter, but 913 and 915 are also flagged as "maintenance band" architecture (per the slice doc's own references to slices 913/915). D10 and the Cross-slice Interfaces section make several non-trivial behavioral claims about how 913 and 915 will function once this ships (e.g., "913's 30-test privilege suite runs against the test cluster"). The parent architecture (900) is clear that maintenance slices may touch any layer but cannot rewrite originated contracts. The slice here consumes 913's privileges as an interface without naming the specific contract entry point or failure mode. If 913's privilege suite is later adjusted for its own reasons, this slice's success criterion 4 ("production's `pg_roles` set is byte-identical") could become contradictory without a coordination rule.

### [CONCERN] Production host memory NFR is not restated against architecture targets

The review criteria require that "if the slice touches a path with an NFR stated in the parent architecture document, the NFR is restated in this slice doc with the specific target." The parent 900 arch does not enumerate quantitative NFRs for the host or database (it focuses on CLI/config/logging/provider patterns), so this isn't strictly restating a stated NFR — but the slice itself explicitly raises a production-load NFR ("no test run can make production slower or push the host into swap") in D5 and the Risks section without committing to a measurable target. Two specific values would close the gap: a `shared_buffers` upper bound for the test cluster, and a CPU/load threshold (e.g., a cap on `work_mem × max_connections` against the 92 GB free headroom). Right now D5 says "concrete values belong in the task breakdown," which moves a measurable constraint out of the design doc.

### [CONCERN] I/O and lifecycle failure modes are under-enumerated

The slice enumerates only three risks (accidentally restarting production, host memory, root access) and leaves several I/O failure modes implicit. Per the review criteria, new I/O paths should enumerate hang/timeout/peer-disconnect handling explicitly:
- `pg_createcluster` failure (insufficient privileges, port 5433 already bound) — currently only "PM runs it." No strategy if the chosen port is already taken by a leftover process.
- Test cluster start failure post-provisioning (TimescaleDB preload misconfiguration) — success criterion 8 detects this by running tests, but the doc says nothing about how the slice handles a cluster that starts but immediately exits or refuses connections mid-suite.
- Test-runner concurrent calls during the swap of `MT_TIMESCALE_TEST_URL` (e.g., one agent's run reads the old value while another updates `.env`) — a stale-read hazard that D8's guard addresses only at the misconfiguration level, not for the transition window.
- Behavior if the new cluster's `shared_preload_libraries` edit silently fails and the extension is not loaded — current verification walkthrough step 3 detects this only if explicitly run.

These are addressable by adding a short "Failure modes" subsection (or extending Risks) with each failure and its detection/recovery path.

### [CONCERN] Background-worker failure mode is acknowledged but not bounded

D4 states that `test_policy_advances_head.py` deliberately waits on the real background scheduler rather than calling `run_job()`, and a cluster without background workers would convert those 9 passing tests into hangs or failures. The risk is named but the handling strategy is not explicit: if `timescaledb.max_background_workers` reports 0 at the verification step, what is the recovery action, who runs it, and how is the rest of the suite gated? The verification walkthrough checks the SHOW value but doesn't say what happens on the unhappy branch.

### [NOTE] Verification walkthrough is self-marked as draft

The verification walkthrough is labeled "Draft — to be refined at implementation close." This is fine as a note for tracking, but the cross-references between success criteria 6 ("five consecutive full integration runs") and the bash loop in step 6 should be confirmed before implementation — the loop captures counts but does not assert zero across all five files, leaving the criterion's evidence structure slightly loose for a design doc that elsewhere is unusually precise.
