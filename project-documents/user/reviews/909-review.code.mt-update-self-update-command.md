---
docType: review
layer: project
reviewType: code
slice: mt-update-self-update-command
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/909-slice.mt-update-self-update-command.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260802
dateUpdated: 20260802
findings:
  - id: F001
    severity: fail
    category: correctness
    summary: "`.resolve()` defeats path-segment detection — pipx installs are misclassified as pip"
    location: src/manta_trading/cli/commands/update.py:157
  - id: F002
    severity: concern
    category: error-handling
    summary: "`Version(latest)` can raise `InvalidVersion`, violating the module's own \"never a traceback\" contract"
    location: src/manta_trading/cli/commands/update.py:354
  - id: F003
    severity: note
    category: correctness
    summary: "`_resolve_mt_binary` never finds the sibling entry point"
    location: src/manta_trading/cli/commands/update.py:204-208
  - id: F004
    severity: note
    category: test-coverage
    summary: "Detection tests use non-existent paths, so `.resolve()` is inert in every test"
    location: test/unit/cli/commands/test_update.py:113-152
  - id: F005
    severity: note
    category: naming
    summary: "`REGISTRY_TIMEOUT` is ambiguous in a shared constants module"
    location: src/manta_trading/constants.py:24
  - id: F006
    severity: note
    category: observability
    summary: "No logging on any degradation path"
    location: src/manta_trading/cli/commands/update.py#report_pending_migrations
  - id: F007
    severity: pass
    category: security
    summary: "Security posture of the upgrade path"
    location: src/manta_trading/cli/commands/update.py:190-199
  - id: F008
    severity: pass
    category: project-conventions
    summary: "Constants, enums, and single-definition-site discipline"
    location: src/manta_trading/cli/commands/update.py:31-84
  - id: F009
    severity: pass
    category: error-handling
    summary: "Exception handling meets the project rule"
    location: src/manta_trading/cli/commands/update.py:168-185
---

# Review: code — slice 909

**Verdict:** FAIL
**Model:** claude-opus-5

## Findings

### [FAIL] `.resolve()` defeats path-segment detection — pipx installs are misclassified as pip

`detect_install_method` resolves `sys.executable` before matching path segments. In every real venv (uv tool, pipx, plain venv), `bin/python` is a **symlink to the base interpreter**, so `.resolve()` throws away the very path that carries the `uv/tools` and `pipx/venvs` segments.

Verified on this machine:

```
/Users/manta/.local/share/uv/tools/copier/bin/python
    -> /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12
```

And reproduced directly against the shipped helper using a simulated pipx layout:

```
raw parts match : True
resolved        : /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13
resolved match  : False
```

Consequence: a pipx-installed `mt` falls through `_is_editable_or_source()` (non-editable `direct_url.json`), then `_is_uv_tool_environment()` (pipx writes `pipx_metadata.json`, not `uv-receipt.toml`), then the segment match, and lands on `InstallMethod.PIP`. The user is told to run `pip install --upgrade manta-trading-data` inside a pipx-managed venv — wrong guidance for the one thing this command exists to get right. The uv-tool branch is only saved because b105605 added the `uv-receipt.toml` check ahead of it; that fix addressed the symptom for uv and left the same root cause live for pipx, which now makes `_UV_TOOL_SEGMENTS` (line 157–160) effectively dead code.

Fix: match on the unresolved `Path(sys.executable).parts` (optionally also `Path(sys.prefix).parts`), and detect pipx by the marker file `pipx_metadata.json` at `sys.prefix` — symmetric with the uv receipt check and equally robust to a relocated `PIPX_HOME`.

### [CONCERN] `Version(latest)` can raise `InvalidVersion`, violating the module's own "never a traceback" contract

`fetch_latest_version` documents that "a registry outage or a malformed payload must never surface a traceback" (line 168–174), but its validation stops at `isinstance(version, str) and version` (line 184). A non-PEP-440 string passes that gate and reaches `Version(latest)` at line 354, where `packaging` raises `InvalidVersion` — an uncaught exception producing a raw traceback on the `mt update` happy path. The guard is one step short of the contract it claims to enforce.

Same unvalidated string is then interpolated into `print_result(f"Update available: {current} → {latest}", ...)` at line 374, which routes through `rich.print` and parses markup — a version string containing `[` can raise `MarkupError` or emit styled output from remote data.

Both are closed by the same change: parse with `Version(version)` inside the existing `try` in `fetch_latest_version` (`InvalidVersion` subclasses `ValueError`, so the existing tuple already covers it) and return `None` on failure. Add a `test_fetch_non_pep440_returns_none` case alongside `test_fetch_mistyped_version_returns_none`.

### [NOTE] `_resolve_mt_binary` never finds the sibling entry point

Same root cause as the first finding: `Path(sys.executable).resolve().parent / "mt"` points at the *base interpreter's* bin directory, not the venv's, so the candidate never exists and the function always degrades to bare `mt` on `PATH`. Behaviour is safe (PATH `mt` is usually correct), but the documented preference — "the `mt` entry point beside this interpreter" — never actually fires. Dropping `.resolve()` here makes the docstring true.

### [NOTE] Detection tests use non-existent paths, so `.resolve()` is inert in every test

`test_detect_uv_tool_path` and `test_detect_pipx_path` set `sys.executable` to synthetic absolute paths that do not exist on disk. `Path.resolve(strict=False)` on a non-existent path is a pure lexical normalization, so the symlink hop that breaks production never occurs and both tests pass against broken code. This is the false-confidence pattern CLAUDE.md calls out ("the test fixture must include the actual format that parser will consume in production"). A regression test should build a real `tmp_path` venv layout with `bin/python` symlinked to `sys.executable` — that test fails today and passes after the fix.

### [NOTE] `REGISTRY_TIMEOUT` is ambiguous in a shared constants module

"Registry" is overloaded in this codebase (provider registry vs. package index). The neighbouring constants are explicitly scoped (`PYPI_JSON_URL_TEMPLATE`, `UPDATE_MIGRATE_PROBE_TIMEOUT`); `PYPI_REGISTRY_TIMEOUT` would match and remove the ambiguity at the one call site.

### [NOTE] No logging on any degradation path

Every failure in `fetch_latest_version` and `report_pending_migrations` collapses to `None` with no record of *which* failure occurred. The user-facing behaviour is correct and explicit (an error line plus exit 1, or the generic migration pointer), so this does not violate the exception-handling rule — but a `logger.debug` carrying the exception would make "PyPI unreachable" versus "PyPI returned garbage" distinguishable in a support report. The module already avoids the DB layer; `manta_trading.logging.get_logger` is import-safe here.

### [PASS] Security posture of the upgrade path

Fixed argv, `shell=False`, no interpolation of registry-supplied data into the command, HTTPS-only endpoint, both subprocesses bounded by explicit timeouts, and `shutil.which` gating before exec. `--json` is a genuinely pure query — `test_cli_json_is_a_pure_query` asserts zero subprocess calls and zero prompts even with `--yes`. No secrets touched.

### [PASS] Constants, enums, and single-definition-site discipline

`InstallMethod` is a `StrEnum` and all dispatch keys off enum members rather than strings; `MANUAL_UPGRADE_COMMAND` is the single definition site for user-facing command text, and `test_every_install_method_has_manual_command` mechanically enforces exhaustiveness so a new enum member cannot silently lose its guidance. Every timeout lives in `constants.py` with a measured rationale rather than a magic literal. This matches the no-magic-strings and no-hard-coded-defaults rules precisely.

### [PASS] Exception handling meets the project rule

No bare `except:` and no `except Exception: pass`. Every clause enumerates concrete types and carries an inline comment justifying the swallow against a documented contract (D2 return-`None`-on-failure, D6 best-effort probe). `ruff` with `BLE` selected passes clean on both new files, and `mypy` reports no issues.

---

## Resolution (2026-08-02)

All findings addressed. Each fix was verified against a real installation, not
only against unit tests — F004 exists precisely because the original tests
passed while production was broken.

### F001 (FAIL) — fixed and verified end-to-end

Confirmed, and broader than reported: the symlink collapse defeats detection in
the **default** uv location too, not only relocated ones —
`~/.local/share/uv/tools/copier/bin/python` resolves to
`/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12`. The earlier
`uv-receipt.toml` fix (b105605) had attributed the failure to `UV_TOOL_DIR`
relocation, which was the wrong root cause; it masked the symptom for uv and
left pipx broken.

Reproduced in production form: the published `0.7.1` installed with real
pipx 1.16.5 reports `install_method: "pip"` and prints
`pip install --upgrade manta-trading-data` inside a pipx-managed venv.

Fix as recommended, plus the symmetric pipx marker:

- segments now match **unresolved** `Path(sys.prefix).parts +
  Path(sys.executable).parts` (`_environment_parts`);
- `pipx_metadata.json` at `sys.prefix` detects pipx, verified against a real
  pipx 1.16.5 venv, symmetric with the uv receipt and robust to a relocated
  `PIPX_HOME`;
- marker checks run before segment checks, which remain as a fallback.

Verified after the fix: the same wheel installed with real pipx reports
`install_method: "pipx"`.

### F002 (CONCERN) — fixed

`Version(version)` now parses inside the existing `try` in
`fetch_latest_version`; `InvalidVersion` subclasses `ValueError`, so the
existing tuple covers it and the function returns `None`. This closes the Rich
markup vector too — a valid PEP 440 version cannot contain `[`. Added
`test_fetch_non_pep440_returns_none` and
`test_fetch_markup_bearing_version_returns_none`.

### F003 (NOTE) — fixed

`.resolve()` dropped from `_resolve_mt_binary`; the docstring's stated
preference now actually fires.

### F004 (NOTE) — fixed

`test_detect_survives_symlinked_interpreter` builds a real `tmp_path` venv
layout with `bin/python` symlinked to `sys.executable`, parametrized over the
uv and pipx layouts. Confirmed to be a genuine regression test: run against the
pre-fix algorithm the same fixture yields `pipx match: False`.

### F005 (NOTE) — fixed

`REGISTRY_TIMEOUT` → `PYPI_REGISTRY_TIMEOUT`, with the ambiguity noted in its
docstring.

### F006 (NOTE) — fixed

`logger.debug` on every degradation path in `fetch_latest_version`,
`report_pending_migrations`, and `installed_version`, each distinguishing which
failure occurred. `get_logger` is import-safe and adds no startup cost (D9).

### Shipped where

`0.7.0`–`0.7.2` on PyPI all carry F001. The fixes above shipped in `0.7.3`
(tag `v0.7.3`), published on PyPI.
