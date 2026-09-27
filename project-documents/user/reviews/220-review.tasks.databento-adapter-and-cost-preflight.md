---
docType: review
layer: project
reviewType: tasks
slice: databento-adapter-and-cost-preflight
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/220-tasks.databento-adapter-and-cost-preflight.md
aiModel: z-ai/glm-5.3-flash
status: complete
dateCreated: 20260927
dateUpdated: 20260927
reviewedSha: 6d61219387eb7553a3d573a56a4ad7a7cdc89f35
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 11
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "The \"malformed response → ProviderPermanentError\" arm of the free-call mapping has no test"
    location: "project-documents/user/tasks/220-tasks.databento-adapter-and-cost-preflight.md:194"
  - id: F002
    severity: concern
    category: traceability
    summary: "The \"batch retention figure recorded\" criterion has no task owning the wiring between the account read and the Recorded Results entry"
    location: "project-documents/user/tasks/220-tasks.databento-adapter-and-cost-preflight.md:431"
  - id: F003
    severity: note
    category: task-sizing
    summary: "Task 4.7 is the largest implementation task and should stay watch-listed for a split"
    location: "project-documents/user/tasks/220-tasks.databento-adapter-and-cost-preflight.md:279"
  - id: F004
    severity: note
    category: coverage
    summary: "No load-test task, correctly — the slice design explicitly excludes that tier"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:288"
  - id: F005
    severity: pass
    category: traceability
    summary: "All eleven functional success criteria have covering tasks, and every task traces back to a criterion"
    location: "project-documents/user/tasks/220-tasks.databento-adapter-and-cost-preflight.md:70"
  - id: F006
    severity: pass
    category: sequencing
    summary: "Sequencing, granularity, and commit distribution are sound"
    location: "project-documents/user/tasks/220-tasks.databento-adapter-and-cost-preflight.md:73"
---

# Review: tasks — slice 220

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.3-flash

## Findings

### [CONCERN] The "malformed response → ProviderPermanentError" arm of the free-call mapping has no test

Task 3.4 (line 194) specifies "any other `BentoClientError` or a malformed response → `ProviderPermanentError`", tracing to Technical Decision 10's free-call mapping in the slice design. Task 3.6 (lines 198–221), the tests that follow it, enumerates only `5xx, 429, timeout → Transient; 401, 403 → Auth; 400 → Permanent` — no malformed-response case. This is the one arm of the mapping with no concrete SDK exception to catch (it fires on a response the adapter cannot parse, not on a `BentoError` class), so it is exactly the arm a junior implementer is most likely to leave dead or silently untested. The design's I9 row (loud failure), which Task 7.6 cites, makes this arm load-bearing. Add a malformed-response test to Task 3.6's bullets.

### [CONCERN] The "batch retention figure recorded" criterion has no task owning the wiring between the account read and the Recorded Results entry

The slice's Technical Requirements state "the task file records … the batch retention figure and any per-mode (batch, direct) size limits as read from the account", and the design's findings table explicitly defers this to "recorded as a task once the PM's account exists". Task 7.4 (lines 431–433) reads the figures into *Recorded Results*, which is correct coverage — but the criterion's other half, that 223's design depends on this figure being available, is only carried implicitly. There is no sub-bullet in 7.4 (nor in 7.8's line-by-line criterion review, lines 450–453) confirming the figure is readable by a 223 implementer who never opens this task file, and the "if a figure cannot be found, write 'not published'" instruction is the sole guard against a silently guessed value. Add to Task 7.4: state in *Recorded Results* which 223 decision the figure feeds (the retention check the design assigns to 223's design) so the linkage is explicit rather than tribal knowledge.

### [NOTE] Task 4.7 is the largest implementation task and should stay watch-listed for a split

Task 4.7 (verified `download_batch`, effort 3) carries seven distinct behaviors from the slice design's `download_batch` success-criterion bullet: full transfer, resume-with-`Range`, equal-size-no-request, oversize-restart, `200`-to-ranged, `416`, checksum mismatch, and stall-timeout. It is the only implementation task at effort 3 whose test counterpart (Task 4.8, also effort 3) enumerates eight separate cases. It is not over the line — the task already names the escape hatch (`_download.py` extraction) and the design's risk section calls it "one small module-level concern" — but if implementation shows it breaching ~300 lines or the test task ballooning, split it into "read-and-fetch" and "resume-and-verify" rather than stretching one task. No action required now.

### [NOTE] No load-test task, correctly — the slice design explicitly excludes that tier

The slice's Technical Requirements state "No integration or load tier work — nothing touches a database", so the parent-level rule "if the parent slice restates an NFR, a load test task exists" does not trigger: no NFR is restated here, and the only performance artifact (the decode benchmark, Tasks 6.1–6.2) is a manual measurement recorded in the task file, not a `tests/load/` gate — matching the design's own statement that the benchmark "can only fail the target, never pass it" and that 226 decides it on purchased data. No CI-wiring task is owed for this slice. Verified against the architecture parent, whose throughput/contingion language sits in later slices (222/226), not this one.

### [PASS] All eleven functional success criteria have covering tasks, and every task traces back to a criterion

Mapping the slice's Success Criteria (slice design lines 275–293) to tasks: CLI estimate rows and all four exit-code cases → Tasks 5.1/5.3/5.5; `dataset_condition` exclusive-to-inclusive conversion → Task 3.5 plus its assertion in 3.6; ceiling semantics including the tier-within/bundle-over case → Tasks 1.2/1.5/1.6 and 5.1/5.2; `DbnFileReader` on all five sample files with itemsizes, mappings, and patched-budget batching → Tasks 2.1/2.3/2.4; the `build_estimate` backstop with `timeseries`/`batch` raising on access → Task 5.2's backstop bullet plus Task 5.6's import-boundary test (the mypy half of that criterion is enforced by Task 5.7's checkpoint mypy run and Task 7.8's full-tier run); both paid methods' outcome-unknown mapping and `fetch_range`'s file hygiene → Tasks 4.2–4.6; all eight `download_batch` cases against `httpx.MockTransport` → Task 4.8; context-manager close on normal and exceptional exit → Tasks 3.3/3.6; recorded fixtures committed and no hand-written metadata response remaining → Tasks 7.1–7.3; zero portal spend → Task 7.7 step 8. Technical criteria map to Tasks 1.1 (lock/commit together), 1.2/5.6 (single-definition values and import boundary), 3.1 (exclusive-end docstring), 5.3 (exit codes defined once), 7.5/7.6 (README, `.env_sample`, I10/I9 contract rows), 2.1 (`SOURCES.md` provenance), 6.2/7.4 (recorded benchmark and account facts). Integration criteria (222/223/224 consumability) are satisfied by the protocol tasks 2.2, 3.1, and 4.1, which build exactly the vocabulary the design's "Provides to Other Slices" section names. No orphan tasks found: every task cites a design section by name in its parent heading or sub-bullets.

### [PASS] Sequencing, granularity, and commit distribution are sound

Sections 1–7 follow the design's Development Approach in order, with no forward references: constants (1.2) precede the reader that reads them (2.3); `provider.py` protocols (2.2, 3.1, 4.1) precede the adapter methods that implement them (3.5, 4.3, 4.5, 4.7); the estimate core (5.1) precedes the CLI that renders it (5.3–5.4); and the benchmark (6.1) precedes running it (6.2). Every implementation task is immediately followed by its test task (1.2→1.3, 1.5→1.6, 2.3→2.4, 3.3–3.5→3.6, 4.3/4.5/4.7→4.4/4.6/4.8, 5.1–5.4→5.5), each with a machine-checkable success line a junior AI can evaluate. Section checkpoints (1.7, 2.5, 3.7, 4.9, 5.7) put a commit in every section plus two additional commits in Section 7, so nothing is batched at the end — matching both the CLAUDE.md "commit at least once per task" rule and the design's "commit at least once per step". No task is circularly dependent, and the one external blocking dependency (the PM's Databento account for Section 7) is called out in the task file's frontmatter and Section 7 heading rather than buried.

### Run Digest

- Response length: 8003 chars
- Response is newline-free: no
- Tool calls made: 11
- Tool calls failed: 0
- Stop reason: stop
- Reasoning characters: 1496
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
