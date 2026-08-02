---
docType: review
layer: project
reviewType: code
slice: pypi-distribution-and-production-cutover
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/908-slice.pypi-distribution-and-production-cutover.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260801
dateUpdated: 20260801
findings:
  - id: F001
    severity: concern
    category: typing
    summary: "Missing return type annotation on new test_version_reports_resolved_distribution_metadata"
    location: test/unit/test_cli_app.py:42-49
  - id: F002
    severity: concern
    category: typing
    summary: "Missing return type annotation on test_version_falls_back_to_dev_and_warns_on_missing_metadata"
    location: test/unit/test_cli_app.py:51-65
  - id: F003
    severity: concern
    category: typing
    summary: "Missing parameter and return type annotations on test_deleted_commands_are_not_invocable"
    location: test/unit/test_cli_data.py:52-62
  - id: F004
    severity: pass
    category: uncategorized
    summary: "DISTRIBUTION_NAME constant is well-placed and documented"
    location: src/manta_trading/constants.py:11-19
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Version callback now logs a warning instead of silently falling back"
    location: src/manta_trading/cli/app.py:14-46
  - id: F006
    severity: pass
    category: uncategorized
    summary: "Test coverage for 404-as-EMPTY behavior is now consistent"
    location: test/unit/data/acquisition/test_outcomes.py:88-99, test/unit/data/acquisition/daemon/test_daily.py:187-197
  - id: F007
    severity: pass
    category: uncategorized
    summary: "Docstring update to classify_outcome accurately documents 404 behavior"
    location: src/manta_trading/data/acquisition/outcomes.py:60-65
  - id: F008
    severity: pass
    category: uncategorized
    summary: "Replaced brittle help-text check with precise invocation check"
    location: test/unit/test_cli_data.py:49-62
---

# Review: code — slice 908

**Verdict:** CONCERNS
**Model:** minimax/minimax-m3

## Findings

### [CONCERN] Missing return type annotation on new test_version_reports_resolved_distribution_metadata

The new test method `test_version_reports_resolved_distribution_metadata` lacks a `-> None` return type annotation and does not annotate the `monkeypatch` fixture. The existing `TestVersion.test_version` directly above it is annotated as `def test_version(self) -> None`, so the new method is inconsistent with the file's style and violates the project's "Type hint all function signatures" rule. Should be `def test_version_reports_resolved_distribution_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:`.

### [CONCERN] Missing return type annotation on test_version_falls_back_to_dev_and_warns_on_missing_metadata

Same issue: this new test method is missing both the `-> None` return annotation and parameter annotations for `monkeypatch` and `caplog`. With these fixes the new tests will be consistent with the surrounding test class and comply with the project's strict pyright configuration.

### [CONCERN] Missing parameter and return type annotations on test_deleted_commands_are_not_invocable

The new parametrized test method introduces an untyped `cmd` parameter and lacks a return type annotation. Should be `def test_deleted_commands_are_not_invocable(self, cmd: str) -> None:`. The accompanying `@pytest.mark.parametrize` provides string values so a `str` annotation is the right fit. While the removed test also lacked a return type, the new test adds a new typed parameter (`cmd`) that should be annotated per the project's rules.

### [PASS] DISTRIBUTION_NAME constant is well-placed and documented

Adding `DISTRIBUTION_NAME: Final[str] = "manta-trading-data"` with a docstring that explicitly disambiguates the distribution name from the import package name (`manta_trading`) and notes the deliberate divergence from config paths is a good example of "Never scatter comparison values across code" from `CLAUDE.md`. The `Final` marker prevents accidental rebinding.

### [PASS] Version callback now logs a warning instead of silently falling back

The replacement of the hardcoded `"manta-trading"` with `DISTRIBUTION_NAME`, plus the addition of `logger.warning(...)` on `PackageNotFoundError`, directly addresses `CLAUDE.md`'s "Never use silent fallback values" rule. The warning message uses `%r` formatting for the name and explains the fallback rationale. The handler catches the specific `PackageNotFoundError` (not a bare `except`) and the CLI entry point qualifies as a process boundary, so the handling is compliant with the project's exception-handling policy.

### [PASS] Test coverage for 404-as-EMPTY behavior is now consistent

The refactor splits the 4xx cases into two tests: the parametrized `test_4xx_non_429_raises_provider_error` no longer includes 404, and a new `test_http_404_is_empty` covers the EMPTY classification. The daemon-level tests are also split into `test_404_recorded_as_empty` and `test_4xx_non_429_non_404_recorded_as_transient_failure`, with the latter's docstring correctly explaining why the exception does not propagate out of `run_daily_cycle` (it is caught per-symbol). This is test-with rather than test-after behavior, which the project prefers.

### [PASS] Docstring update to classify_outcome accurately documents 404 behavior

The `Raises` section now calls out the 404 exception with a cross-reference ("404 is classified as EMPTY, see below"), which is the right place to surface this contract for callers reading the API.

### [PASS] Replaced brittle help-text check with precise invocation check

The previous `test_data_help_does_not_show_deleted_commands` was too broad (it scanned help output for the bare words "daily", "minute", "refetch") and could produce false positives if a legitimate command mentioned those terms in its description. The new parametrized `test_deleted_commands_are_not_invocable` invokes each removed command and asserts a "No such command" error. The docstring explicitly explains the rationale (mentioning the `rechunk` overlap with `minute_ohlcv`). This is a good test-refinement, not just a rewrite.
