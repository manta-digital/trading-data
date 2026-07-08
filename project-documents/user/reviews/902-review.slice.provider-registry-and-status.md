---
docType: review
layer: project
reviewType: slice
slice: provider-registry-and-status
project: squadron
verdict: PASS
sourceDocument: project-documents/user/slices/902-slice.provider-registry-and-status.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260330
dateUpdated: 20260330
---

# Review: slice — slice 902

**Verdict:** PASS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] Provider Registry Implementation Matches Architecture

The slice implements all required components from the architecture's **Provider Registry** section:

- **`ProviderType` enum** with ALPHA_VANTAGE, DATABENTO, FLAT_FILE using `StrEnum` (per architecture's "no magic strings" principle)
- **`ProviderProfile` frozen dataclass** with all specified fields: `name`, `provider_type`, `base_url`, `api_key_env`, `rate_limit`, `aliases`, `description`
- **`AuthStrategy` protocol** with `is_valid`, `active_source`, and `setup_hint` methods
- **CLI commands** `mt provider list/status/test` and `mt status` for introspection

Reference: Architecture section "Provider Registry", slice Technical Scope

### [PASS] Layer Responsibilities Properly Segregated

The slice correctly scopes itself to the registry layer without overstepping:

- **This slice (902)**: Registry definition, profile lookup, auth status checking, CLI commands for introspection
- **Slice 903**: Actual provider implementation wiring, strict credential enforcement at construction time
- **Excluded**: Provider HTTP clients (existing `api/` code), DB schema changes, wiring existing AlphaVantage to registry

The architecture states that missing credentials should raise "at provider instantiation" — the slice correctly defers this enforcement to slice 903 via `AuthStrategy.is_valid()` check, rather than duplicating construction-time validation in the registry layer.

Reference: Technical Decisions section on "ApiKeyAuthStrategy", Integration Points section

### [PASS] Dependencies Are Correct and Properly Stated

The slice declares correct dependencies:

- **Slice 900** (`cli/app.py`, `Settings` class, config system) — consumed via imports
- **Slice 901** (`get_logger`, `print_result`, `print_error`, `make_table`) — consumed via imports
- **Slice 903** depends on this slice — correctly documented as a consumer of `ProviderType`, `get_profile()`, `resolve_auth()`
- No backward dependencies to lower-numbered slices

Reference: Dependencies section, Integration Points section ("Provides to Other Slices")

### [PASS] CLI is the Verification Surface

The design follows the architecture's "CLI is the verification surface" principle:

- All commands have `--json` output modes for machine consumption
- `mt provider list/status/test` commands provide human-readable introspection
- `mt status` provides top-level system health view
- Verification Walkthrough demonstrates all CLI interactions with expected output

Reference: Architecture design goal "Discoverable CLI", Technical Decisions section on CLI Commands

### [PASS] No String-Based Dispatch

The design explicitly prohibits string-based provider dispatch:

> "No string-based provider dispatch anywhere in new code — all dispatch uses `ProviderType` enum"

The `resolve_alias()` function maps aliases to canonical names, which are used to look up `ProviderProfile`, whose `provider_type` field is the enum used for all dispatch. This follows the "no magic strings" architectural principle.

Reference: ProviderProfile section, Success Criteria item 14

### [CONCERN] User-Defined Profiles via TOML Deferred Without Tracking

The architecture mentions "Built-in profiles + user-defined profiles via TOML config" but the slice explicitly defers user-defined profiles:

> "User-defined profiles via TOML — deferred until there's demand; built-in profiles are sufficient for current providers"

This is a reasonable scope reduction for initial implementation. However, the slice doesn't document how user-defined profiles would integrate later (e.g., will they use the same `BUILT_IN_PROFILES` dict pattern, or a separate `USER_PROFILES`?). This could create an integration burden for future slices that need this feature.

**Recommendation**: Add a brief note in the Implementation Notes section outlining the expected extension point for user-defined profiles, even if not implemented in this slice.

Reference: Excluded section, Architecture Provider Registry description

### [PASS] Implementation Order Follows Good Dependency Practices

The suggested implementation order is sound:

1. Types and profiles (foundation, stdlib only)
2. Auth (depends on types/profiles)
3. Errors (standalone)
4. Unit tests for modules
5. CLI commands (depends on all above + output utilities)
6. CLI tests
7. Wire into app
8. Integration verification

This ensures each layer can be tested in isolation before integration.

Reference: Implementation Notes section
