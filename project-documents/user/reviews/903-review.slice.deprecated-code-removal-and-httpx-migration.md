---
docType: review
layer: project
reviewType: slice
slice: deprecated-code-removal-and-httpx-migration
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/slices/903-slice.deprecated-code-removal-and-httpx-migration.md
aiModel: claude-haiku-4-5-20251001
status: complete
dateCreated: 20260331
dateUpdated: 20260331
---

# Review: slice — slice 903

**Verdict:** CONCERNS
**Model:** claude-haiku-4-5-20251001

## Findings

### [PASS] Scope alignment with Architecture 900 goals

Slice 903 properly addresses all required foundation cleanup goals from Architecture 900:
- Removes deprecated code paths (`market/deprecated/` directory, old CLI entry points)
- Implements httpx migration as specified ("httpx replaces aiohttp in the AlphaVantage client")
- Establishes the `mt data` sub-app pattern for future initiatives
- All work falls squarely within "deprecated code removal" scope anticipated by Architecture 900

### [PASS] Architectural principles enforced

The slice correctly implements all stated principles:
- **No magic strings**: Uses `ProviderType` enum from Slice 902 for dispatch; no string-based provider identification
- **Explicit failure**: Credential validation via `resolve_auth()` before API calls, with actionable error messages
- **CLI is verification surface**: All daily pipeline operations accessible through `mt data daily` commands
- **Minimal dependencies**: httpx already declared in `pyproject.toml`; no new dependency bloat

### [PASS] Proper dependency direction with Slice 900 and Slice 902

- Slice 900 (CLI scaffold, Settings class): ✓ Correctly listed as prerequisite
- Slice 902 (provider registry): ✓ Correctly listed as prerequisite
- Both prerequisite slices (900, 902) are marked complete and their interfaces are properly used:
  - Settings for credentials and configuration
  - `get_profile()` and `resolve_auth()` for provider validation

### [PASS] Integration points correctly specified

- Provides: `mt data` sub-app pattern for Initiative 100 and future data commands
- Consumes: Settings (900), provider registry (902), structured output utilities
- Interface with Slice 904 properly declared (unblocks packaging and version work)

### [PASS] Scope is properly bounded with no creep

Explicitly excludes:
- Minute data CLI commands (depend on Slice 750/751)
- News CLI subcommands (deferred to separate initiative)
- Database schema changes
- API client internals (rate limiter, task queue)
- Redesign of daily pipeline logic (wrapping only)

These exclusions align with Architecture 900's principle of "CLI is the verification surface" while deferring out-of-scope work appropriately.

### [CONCERN] Incomplete transitive dependency documentation

Slice 903 declares `dependencies: [900, 902]` but directly requires interfaces from Slice 901:
- `get_logger` from `manta_trading.logging`
- `print_result`, `print_error`, `make_table` from `manta_trading.cli.output`

**Analysis**: While Slice 901 is complete and explicitly lists [902, 903, 904] in its interfaces field, Slice 903's dependency documentation creates a hidden transitive dependency. The dependency graph would be clearer if Slice 903 either:
1. Listed `[900, 901, 902]` explicitly, or
2. Included a note that Slice 901 (logging/output) is transitively required

This is architectural documentation clarity, not a functional blocker — all required modules exist and are complete. However, making slice dependencies explicit improves maintainability and prevents subtle coordination issues if future slices modify the dependency chain.

### [PASS] Technical decisions are sound and well-documented

The httpx migration strategy is detailed with proper mapping table, session management pattern preserved, and error handling updated. Risk assessment is present with mitigations identified (focused tests for timeout handling, resource cleanup verification).

---

## Debug: Prompt & Response

### System Prompt

You are an architectural reviewer. Your task is to evaluate whether a design
document aligns with a parent architecture document and its stated goals.

Evaluation criteria:
- Alignment with stated architectural goals and principles
- Violations of architectural boundaries or layer responsibilities
- Scope creep beyond what the architecture defines
- Dependency directions are correct
- Integration points match what consuming/providing slices expect
- Common antipatterns: over-engineering, under-specification, hidden dependencies

Important context:
- The `parent` field in slice frontmatter refers to the slice plan document,
  not the architecture document. Do not flag this as an error.

CRITICAL: Your verdict and findings MUST be consistent.
- If verdict is CONCERNS or FAIL, include at least one finding with that severity.
- If no CONCERN or FAIL findings exist, verdict MUST be PASS.
- Every finding MUST use the exact format: ### [SEVERITY] Title

Report your findings using severity levels:

## Summary
[overall assessment: PASS | CONCERNS | FAIL]

## Findings

### [PASS|CONCERN|FAIL] Finding title
Description with specific references.


### User Prompt

Review the following document for architectural alignment:

**Input document:** project-documents/user/slices/903-slice.deprecated-code-removal-and-httpx-migration.md
**Architecture document:** project-documents/user/architecture/900-arch.foundation-cleanup.md

Read both documents, then evaluate the input against the architecture.
Follow referenced files as needed to understand dependencies and integration points.
Report your findings using the severity format described in your instructions.


### Rules Injected

None

### Raw Response

I'll review these documents for architectural alignment. Let me start by reading both files.Using tool: ReadUsing tool: ReadLet me check the actual file locations:Using tool: GlobUsing tool: GlobNow let me read the documents with the correct paths:Using tool: ReadUsing tool: ReadNow let me examine the referenced dependencies to better understand integration points:Using tool: GlobUsing tool: GlobUsing tool: GlobUsing tool: ReadUsing tool: ReadLet me check the slice plan document referenced by Slice 903:Using tool: ReadLet me check if Slice 901 exists to verify it provides the required logging and output interfaces:Using tool: GlobUsing tool: ReadNow let me check if Slice 904 is referenced to understand the interface requirements:Using tool: GlobBased on my review of Slice 903 against the Architecture 900 document and related slices, here are my findings:

## Summary
**CONCERNS**

## Findings

### [PASS] Scope alignment with Architecture 900 goals
Slice 903 properly addresses all required foundation cleanup goals from Architecture 900:
- Removes deprecated code paths (`market/deprecated/` directory, old CLI entry points)
- Implements httpx migration as specified ("httpx replaces aiohttp in the AlphaVantage client")
- Establishes the `mt data` sub-app pattern for future initiatives
- All work falls squarely within "deprecated code removal" scope anticipated by Architecture 900

### [PASS] Architectural principles enforced
The slice correctly implements all stated principles:
- **No magic strings**: Uses `ProviderType` enum from Slice 902 for dispatch; no string-based provider identification
- **Explicit failure**: Credential validation via `resolve_auth()` before API calls, with actionable error messages
- **CLI is verification surface**: All daily pipeline operations accessible through `mt data daily` commands
- **Minimal dependencies**: httpx already declared in `pyproject.toml`; no new dependency bloat

### [PASS] Proper dependency direction with Slice 900 and Slice 902
- Slice 900 (CLI scaffold, Settings class): ✓ Correctly listed as prerequisite
- Slice 902 (provider registry): ✓ Correctly listed as prerequisite
- Both prerequisite slices (900, 902) are marked complete and their interfaces are properly used:
  - Settings for credentials and configuration
  - `get_profile()` and `resolve_auth()` for provider validation

### [PASS] Integration points correctly specified
- Provides: `mt data` sub-app pattern for Initiative 100 and future data commands
- Consumes: Settings (900), provider registry (902), structured output utilities
- Interface with Slice 904 properly declared (unblocks packaging and version work)

### [PASS] Scope is properly bounded with no creep
Explicitly excludes:
- Minute data CLI commands (depend on Slice 750/751)
- News CLI subcommands (deferred to separate initiative)
- Database schema changes
- API client internals (rate limiter, task queue)
- Redesign of daily pipeline logic (wrapping only)

These exclusions align with Architecture 900's principle of "CLI is the verification surface" while deferring out-of-scope work appropriately.

### [CONCERN] Incomplete transitive dependency documentation
Slice 903 declares `dependencies: [900, 902]` but directly requires interfaces from Slice 901:
- `get_logger` from `manta_trading.logging`
- `print_result`, `print_error`, `make_table` from `manta_trading.cli.output`

**Analysis**: While Slice 901 is complete and explicitly lists [902, 903, 904] in its interfaces field, Slice 903's dependency documentation creates a hidden transitive dependency. The dependency graph would be clearer if Slice 903 either:
1. Listed `[900, 901, 902]` explicitly, or
2. Included a note that Slice 901 (logging/output) is transitively required

This is architectural documentation clarity, not a functional blocker — all required modules exist and are complete. However, making slice dependencies explicit improves maintainability and prevents subtle coordination issues if future slices modify the dependency chain.

### [PASS] Technical decisions are sound and well-documented
The httpx migration strategy is detailed with proper mapping table, session management pattern preserved, and error handling updated. Risk assessment is present with mitigations identified (focused tests for timeout handling, resource cleanup verification).
