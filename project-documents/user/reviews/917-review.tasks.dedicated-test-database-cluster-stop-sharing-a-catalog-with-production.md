---
docType: review
layer: project
reviewType: tasks
slice: dedicated-test-database-cluster-stop-sharing-a-catalog-with-production
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/tasks/917-tasks.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260819
dateUpdated: 20260819
reviewedSha: 2073a31f156a1315b08c64cd3e9cbf17610f53e7
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "All ten success criteria map to tasks"
    location: "917-tasks.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md"
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Test-with pattern correctly applied for guard changes"
    location: "917-tasks.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md:230-252"
  - id: F003
    severity: pass
    category: uncategorized
    summary: "PM/agent boundary is explicit and consistently applied"
    location: "917-tasks.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md:36-40"
  - id: F004
    severity: pass
    category: uncategorized
    summary: "TimescaleDB gate placed before any test run, not after"
    location: "917-tasks.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md:120-135"
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Production invariants anchored to A-baseline / F-verification pair"
    location: "917-tasks.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md:52-86"
  - id: F006
    severity: pass
    category: uncategorized
    summary: "Recovery scenarios from design Failure modes table addressed"
    location: "917-tasks.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md"
  - id: F007
    severity: note
    category: uncategorized
    summary: "F.1 does not call out the \"void run\" rule from the failure modes table"
    location: "917-tasks.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md:267-274"
  - id: F008
    severity: note
    category: uncategorized
    summary: "G.2 does not trace to a success criterion"
    location: "917-tasks.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md:331-338"
  - id: F009
    severity: note
    category: uncategorized
    summary: "Commit checkpoints are sparse between groups"
    location: "917-tasks.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md"
  - id: F010
    severity: note
    category: uncategorized
    summary: "G.4 follow-up capture target is unspecified"
    location: "917-tasks.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md:347-354"
  - id: F011
    severity: pass
    category: uncategorized
    summary: "No NFR-driven load test required"
    location: "917-slice.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md"
---

# Review: tasks — slice 917

**Verdict:** PASS
**Model:** minimax/minimax-m3

## Findings

### [PASS] All ten success criteria map to tasks

Each of the slice's success criteria has explicit coverage: SC1 (B.1/B.4), SC2 (B.6), SC3 (B.5), SC4 (A.1/C.3/C.4/F.6), SC5 (D.1/B.7), SC6 (F.1), SC7 (A.3/F.2), SC8 (F.3), SC9 (E.1/E.2/E.3), SC10 (A.2/F.6). The "Definition of done" at the bottom of the file explicitly enumerates the ten criteria, confirming the mapping is intentional rather than coincidental.

### [PASS] Test-with pattern correctly applied for guard changes

Group E follows the test-with pattern: E.1 and E.2 are implementation, E.3 is the verification test for both guards (with the explicit anti-pattern-revert check), E.4 is the commit. The tests are co-committed with the implementation in E.4 per the project rule that commit messages describe what the guards prevent.

### [PASS] PM/agent boundary is explicit and consistently applied

Tasks requiring root are marked **[PM]** at the operations that need it (B.1, B.3, B.4, C.1, C.2) and verification steps are correctly assigned to **[agent]**. The Execution note "Do not attempt to work around a [PM] task" closes the loophole the design identified in Risks.

### [PASS] TimescaleDB gate placed before any test run, not after

B.5 is the gate task and it is positioned at the end of Group B before Group C, before any role provisioning or test execution could mask a misconfiguration. This matches the design's explicit rule that this is a gate, not a post-hoc check, and addresses both failure modes for missing `shared_preload_libraries` and zero `max_background_workers` from the Failure modes table.

### [PASS] Production invariants anchored to A-baseline / F-verification pair

A.1 (roles) and A.2 (postmaster start time) capture before, C.4 re-checks roles mid-slice, and F.6 verifies both at the end. This three-point check satisfies criterion 4 and criterion 10's "across this slice's execution window" requirement, with the interim check at C.4 catching a wrong-cluster provisioning error early.

### [PASS] Recovery scenarios from design Failure modes table addressed

The seven rows of the design's Failure modes table are each handled: pg_createcluster privilege failure (B.1 stop-line), wrong port (B.2 carry-forward), cluster exits immediately (B.4 restart test cluster only), missing preload (B.5 gate), zero background workers (B.5 gate), mid-run connection failure (implicit in F.1), and .env swap race (D.1 explicit "no test run in flight").

### [NOTE] F.1 does not call out the "void run" rule from the failure modes table

The design's failure modes table explicitly states: "Treat the run as void, not as a baseline deviation. A partial run must never be counted toward criterion 6." F.1 instructs the runner on what to assert but does not state the void-run rule. This is a small omission — the rule will likely be remembered in execution but is not written into the task. Adding a sub-bullet ("If a run errors with a connection refusal, discard that log and run again; only five complete runs count") would close the gap.

### [NOTE] G.2 does not trace to a success criterion

G.2 updates a cron-to-systemd forward reference in `backup-and-restore.md` to name slice 916. The slice design does not list this as required, does not list 916 as an interface, and none of the ten success criteria reference this file. This is borderline scope creep. The defense is that it's a quick defensive fix while the slice is touching the same docs surface, but it could also be argued it belongs in slice 916's work. Worth flagging as a judgment call rather than an error.

### [NOTE] Commit checkpoints are sparse between groups

Only three explicit commit tasks exist (A.4, E.4, G.5). The project rule "Git add and commit from project root at least once per task" leaves this open to interpretation, but the design's hostility to leaving host state half-configured (D7, D9, D11) suggests checkpoints after B.4 (test cluster online) and after C.2 (roles provisioned) would make the slice's intermediate states recoverable and reviewable. Not blocking, but the gap between "host has test cluster" and "code committed" spans several groups.

### [NOTE] G.4 follow-up capture target is unspecified

G.4 instructs the runner to capture the follow-up "where slice 913's or 907's owner will find it" but does not name a specific file or path. The design lists this in Out of scope ("Worth raising as follow-up rather than losing"), so the work belongs here; the missing target makes the success criterion ambiguous. A concrete location (e.g., "add an entry to slice 913's task file under its planned follow-ups section, or open an issue with label `slice:917-followup`") would make the task verifiable.

### [PASS] No NFR-driven load test required

The slice design does not restate a non-functional requirement that would mandate a `tests/load/` task. D5's "coverage freshness probe within 10 seconds" and "swap stays at zero" are operational observability targets, both covered by F.4 and F.5 respectively. No CI wiring gap exists because the runner path is unchanged per design D8.
