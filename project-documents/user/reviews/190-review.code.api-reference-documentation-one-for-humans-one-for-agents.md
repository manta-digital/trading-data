---
docType: review
layer: project
reviewType: code
slice: api-reference-documentation-one-for-humans-one-for-agents
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/190-slice.api-reference-documentation-one-for-humans-one-for-agents.md
aiModel: z-ai/glm-5.2
status: complete
dateCreated: 20260917
dateUpdated: 20260917
reviewedSha: 96a2fbf4ed7df0b92be4f2dc7d10f08ddc86846a
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 29
findings:
  - id: F001
    severity: concern
    category: testing
    summary: "Two mutation tests can pass without exercising the gate when the fixture string drifts"
    location: "test/unit/api_server/test_api_docs.py:340-357"
  - id: F002
    severity: concern
    category: testing
    summary: "`test_status_coverage_verdict_shape` asserts nothing when the verdicts list is empty"
    location: "test/integration/test_api_docs_examples.py:78-92"
  - id: F003
    severity: concern
    category: error-handling
    summary: "`blank_fenced_blocks` silently suppresses all markers after an unterminated fence"
    location: "scripts/check_api_docs.py:215-233"
  - id: F004
    severity: note
    category: structure
    summary: "`check_api_docs.py` exceeds the ~300-line source guideline"
    location: "scripts/check_api_docs.py"
  - id: F005
    severity: note
    category: correctness
    summary: "`_documented_number` takes the last number in the prose, which can misidentify the value"
    location: "scripts/check_api_docs.py:519-522"
  - id: F006
    severity: pass
    category: testing
    summary: "Marker parsing, enum token-set comparison, and DB-safe integration fixtures are well-designed"
    location: "scripts/check_api_docs.py:460-475"
---

# Review: code — slice 190

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.2

## Findings

### [CONCERN] Two mutation tests can pass without exercising the gate when the fixture string drifts

Several mutation tests rely on `str.replace` to rewrite a specific substring of the rendered fixture document, but only `test_documenting_a_504_on_credits_fails` guards that the replacement actually happened (`assert "errors: 200, 504" in text`). Two others do not:

- `test_omitting_a_declared_status_passes` does `text.replace("     errors: 200, 422, 504 -->", "     errors: 200 -->")`. If the artifact's status set for that route ever changes (e.g., a `500` is added), the needle vanishes, `replace` is a no-op, the document still matches the schema, and the test passes — asserting nothing about the subset check. The test's stated purpose is to prove the gate permits status omissions; a silent no-op turns it into a tautology.
- `test_malformed_marker_fails_with_its_location` does `text.replace("     params: search:string", "     params: search")`. If `/api/v1/symbols` ever drops or renames `search`, the replace is a no-op, `report` stays green, and the assertion `not report.ok` would *fail* — but only if the assertion is reached; here it is reached, so a no-op makes the test fail rather than pass vacuously. That is the better failure mode, but it still degrades the test from "proof the gate catches a malformed marker" to "proof the fixture still contains `search:string`". An `assert "params: search" in text`-style guard after the rewrite (as the credits test does) would make the dependency explicit.

The file's own docstring says "a gate never observed to fail is not a gate." A mutation test whose mutation silently doesn't apply is the same problem one level up: it observes the gate passing for a reason unrelated to the drift it claims to prove. Add a post-replace assertion that the rewritten substring is present (or that the original is gone), matching the credits test's pattern.

### [CONCERN] `test_status_coverage_verdict_shape` asserts nothing when the verdicts list is empty

The test iterates `response.json()["coverage"]["verdicts"]` and asserts the per-verdict key set and types inside the loop. On the empty `migrated_db` fixture, `CoverageStatus.from_freshness` builds `verdicts` from `CoverageFreshness.verdicts`, which is one-per-`COVERAGE_VIEWS`. If `COVERAGE_VIEWS` is non-empty in production but the fresh ephemeral database yields zero verdicts (e.g., the freshness probe returns none on an empty cagg), the loop body never executes and every shape assertion is skipped — the test passes vacuously. The docstring claims to assert the §3.5 verdict shape, including the `lag_seconds` null-vs-float distinction that was a real bug. Add `assert response.json()["coverage"]["verdicts"]` (non-empty) before the loop, or assert the count matches `COVERAGE_VIEWS`, so an empty list is a failure rather than a silent skip.

### [CONCERN] `blank_fenced_blocks` silently suppresses all markers after an unterminated fence

`blank_fenced_blocks` toggles `inside` on every line matching `FENCE`. If a document has an odd number of fence lines (an unterminated code block — a common authoring mistake), `inside` stays `True` for the remainder of the file and every subsequent line is blanked, including any real endpoint/value markers. The gate then reports every path below the break as undocumented rather than naming the real problem: a broken fence. This contradicts both the project's "lenient parsing over strict matching" guidance and the module's own aim of reporting drift precisely. The fix is small: after the loop, if `inside` is still `True`, emit a `report.fail` (or raise, caught by `run_checks`) naming the unclosed fence. Without it, a markdown author who forgets to close a fence gets a wall of "path not documented" failures that point away from the cause.

### [NOTE] `check_api_docs.py` exceeds the ~300-line source guideline

At 671 lines the gate is well past the CLAUDE.md `~300 lines` guidance. The module is cohesive — artifact reading, document parsing, checks, and orchestration all share state and vocabulary, and splitting would scatter the marker grammar across files. This is a defensible exception, but worth flagging: if marker parsing grows (e.g., a `request-body:` field), consider extracting the parser to its own module before the file becomes hard to navigate.

### [NOTE] `_documented_number` takes the last number in the prose, which can misidentify the value

`_documented_number` returns the last match of `\d[\d,]*(?:\.\d+)?` in the collected prose. For a marker like "The ceiling is 75,000 rows" this is correct. For prose that mentions another number after the tracked value (e.g., "75,000 rows across 10 tables"), it compares `10` to the symbol. The current documents don't do this, so it isn't a live bug, but the heuristic is brittle. A more robust approach would take the number closest to the marker's own line, or require the value to be the only number in the carrier sentence. Documenting the "last number wins" rule in the docstring would at least make the contract explicit for future authors.

### [PASS] Marker parsing, enum token-set comparison, and DB-safe integration fixtures are well-designed

The `check_value_markers` design — enums compare as backticked token sets so a new member fails rather than quietly extending a list, scalars compare by value, unresolvable markers fail rather than skip — directly enforces the "one value, one source" rule from CLAUDE.md. The integration suite's use of `migrated_db`/`kalshi_documented_db` (both descended from `ephemeral_db`'s UUID-named throwaway) and the `create_app(db_url=…)` seam satisfy the production-DB-protection rules structurally rather than by convention. The unit suite's `tree` fixture renders documents from a *pristine* schema copy while tests mutate a separate `schema` copy, so mutations can't leak into the prose and make both sides agree — a subtle trap the fixture explicitly avoids.
