---
docType: review
layer: project
reviewType: arch
slice: foundation-cleanup
project: trading
verdict: CONCERNS
sourceDocument: project-documents/user/architecture/900-arch.foundation-cleanup.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260327
dateUpdated: 20260327
---

# Review: arch — slice 900

**Verdict:** CONCERNS
**Model:** minimax/minimax-m2.7

## Findings

### [CONCERN] Config precedence architecture conflates two systems incompletely

category: completeness
**Location**: Config Layer section, "Centralized configuration" design goal

The document specifies a three-level TOML precedence (CLI flags > project TOML > user TOML > defaults) but doesn't explain how this integrates with pydantic-settings' environment variable handling. The `MT_` prefix environment variables are mentioned as part of Settings, but the precedence ordering is unclear:

1. Are env vars above TOML, below TOML, or do they override at a per-key level?
2. Does pydantic-settings validate the full merged config, or can invalid combinations slip through?
3. How does `mt config set` persist values when TOML takes precedence over user settings?

The document says "Provider credentials via environment variables (never in TOML files)" but doesn't clarify whether this is an exception to the precedence rule or if credentials are simply excluded from the config system entirely.

### [CONCERN] Provider credential access pattern is underspecified

category: completeness
**Location**: Technical Considerations > Provider credentials; Provider Registry section

The architecture describes provider profiles referencing env vars (e.g., "read MT_DATABENTO_API_KEY") but doesn't specify:
- When is the credential read? At provider instantiation? At command invocation?
- Who reads it—Settings class, ProviderProfile, or the provider itself?
- What happens if the referenced env var is missing at access time?

This matters because "Explicit failure" is a stated principle. If a provider tries to access a missing credential, the error message and timing must be predictable. The current spec leaves this to implementation decisions that could be inconsistent.

### [CONCERN] `logger.success()` migration path is missing

category: completeness
**Location**: Current State: "Mixed logging: print(), logger.info() (loguru), and logger.success()"; Logging section

The document acknowledges `logger.success()` exists in the current codebase but provides no migration strategy. Standard Python `logging` has no `.success()` method. Options include:
- Custom logger subclass with `.success()` as `.info()` or a distinct level
- Wrap Rich console output for success messages
- Convert to structured JSON with a "level": "success" field

This is not a trivial decision—success messages are likely used for command confirmation feedback and their formatting/verbosity affects user experience.

### [CONCERN] Version management creates a chicken-and-egg problem for development

category: feasibility
**Location**: Technical Considerations > Version management

`importlib.metadata.version("manta-trading")` requires the package to be installed. During active development:
- Installing the package in editable mode (`uv pip install -e .`) should populate metadata correctly
- But if the workflow involves running `mt` before installation completes, `--version` fails with a cryptic error instead of a helpful message

The document doesn't specify:
- How `--version` behaves when metadata is unavailable (pre-install, broken install)
- The development workflow for contributors

### [CONCERN] Singleton vs. per-command state for Settings and ConfigManager is undefined

category: abstraction
**Location**: Config Layer section

The Settings class and ConfigManager handle application-wide state, but it's unclear whether:
- They are singletons, module-level globals, or created fresh per command invocation
- Multiple commands run in sequence (e.g., scripted `mt config list && mt provider test`) share state or reload

If Settings is a singleton, concurrent command execution could have race conditions. If it's created per command, global state from the first command may not be visible to the second.

### [CONCERN] `ProviderProfile` dataclass is not defined

category: completeness
**Location**: Provider Registry section

The document mentions `ProviderProfile` with "connection details, auth config, rate limits" but provides no schema. What fields exist? How is auth config structured when it references env vars? Without this definition, the provider registry's extension point is abstract rather than concrete.

Future initiatives implementing new providers will need to guess the interface, leading to inconsistent implementations.

### [CONCERN] Async boundary pattern is stated but not justified or validated

category: technology
**Location**: Technical Considerations > Async boundary at CLI

`asyncio.run()` is presented as the pattern for async operations, but the document doesn't address:
- Nested event loops: calling `asyncio.run()` within an already-running event loop (e.g., from a test or jupyter context) raises `RuntimeError`
- Long-running operations: `asyncio.run()` blocks until complete with no cancellation support documented
- Whether any part of the application (third-party libraries, signal handlers) expects an existing event loop

If `marketdb.py` or `marketservice.py` (the existing daily pipeline being integrated) use async internally, the sync CLI wrapper could fail at integration time.

### [CONCERN] Removed deprecated entry points aren't fully specified

category: completeness
**Location**: Package section: "Old CLI entry points (ohlc.py, news.py direct invocation) removed or redirected"

"Removed or redirected" is ambiguous. If redirected, to where? If removed:
- Are they preserved as separate scripts in the package for backward compatibility?
- What breaking change does this represent for existing users/scripted workflows?
- Is there a deprecation period, or is this a hard cut?

External users invoking `python ohlc.py` directly need a migration path.

### [PASS] Config key typing via CONFIG_KEYS is well-specified

category: technology
The `CONFIG_KEYS` typed key definitions address the "No magic strings" principle for configuration. This is a concrete extension point for future config additions.

### [PASS] Provider registry enum-based dispatch follows the design principle

category: abstraction
`ProviderType` enum with alias support is consistent with "No magic strings" and avoids the string-based identification problem in the current AlphaVantage client. The extension point for new providers is clear.

### [PASS] Slice ordering is logically sound

category: dependencies
CLI scaffold → Logging → Provider registry → Cleanup follows correct dependency order. Logging and provider work can build on the config system once established. Cleanup is correctly placed last since it removes old code after the new system is verified.
