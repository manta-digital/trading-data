---
docType: slice-plan
parent: user/architecture/900-arch.foundation-cleanup.md
project: trading
dateCreated: 20260328
dateUpdated: 20260726
status: in-progress
---

# Slice Plan: Foundation & Cleanup

## Parent Document
900-arch.foundation-cleanup.md — Cross-cutting project infrastructure: CLI framework, config, logging, provider registry, deprecated code removal, packaging.

## Foundation Work

1. [x] **(900) CLI Scaffold and Config System** — Typer root app with sub-app structure, `mt` console script entry point, pydantic-settings `Settings` class for env vars, TOML `ConfigManager` with typed `CONFIG_KEYS` and three-level precedence, `mt config set/get/list/path` commands. Establishes the skeleton that all subsequent slices and initiatives hang their commands on. Effort: 3/5

## Feature Slices

2. [x] **(901) Logging and Output Formatting** — Structured logging setup (`setup_logging`, JSON/text formatters, `get_logger` pattern). Shared Rich output formatter with `--json` support for all commands. Migrate existing `print()` and loguru calls to new logging. Dependencies: [900]. Risk: Low. Effort: 2/5

3. [x] **(902) Provider Registry and Status** — `ProviderType` enum, `ProviderProfile` frozen dataclass, built-in profiles for AlphaVantage/DataBento/FlatFile, alias resolution, auth strategy pattern for credential validation. `mt provider list/status/test` commands. `mt status` top-level command showing provider health, DB connectivity, and data freshness summary. Dependencies: [900, 901]. Risk: Low. Effort: 3/5

## Migration / Refactoring Slices

4. [x] **(903) Deprecated Code Removal and httpx Migration** — Delete `market/deprecated/` directory and all imports from it. Remove old CLI entry points (`ohlc.py` direct invocation, `newsoptions.py`). Replace aiohttp with httpx in AlphaVantage client. Wire existing daily pipeline (`marketdb.py`, `marketservice.py`) into new CLI as `mt data daily` subcommands. Dependencies: [900, 902]. Risk: Med. Effort: 3/5

5. [ ] **(905) Lint and Type-Checker Debt Remediation** — Slice 166's code review landed the mandated `[tool.ruff]`/`[tool.ruff.lint]` config (`E,F,W,I,UP,BLE,ASYNC,B`) and a `[tool.mypy]` block in `pyproject.toml`; activating it exposed **1,730 pre-existing violations across `src/` and `test/` (752 autofixable)** that had never been mechanically gated. This slice drives that count to zero so the config gates all code, not just new code by review convention. Scope: (1) apply the 752 ruff autofixes and verify no behavior change (per-subpackage test runs); (2) manually triage the remainder — **note that some findings are latent runtime bugs, not style**: the sweep includes `F821` undefined names in `cli/commands/data.py` (e.g. `datetime`, `psycopg` referenced in code paths where they are not imported — a `NameError` waiting on those paths), plus `F401` unused imports, `E402` late imports, `E501` line lengths, and any `BLE` blind-except violations, each fixed or explicitly `noqa`'d with an inline justification per the exception-handling rules; (3) sweep `raise ... from exc` (B904) across the legacy handlers noted in the 166 review; (4) confirm `uv run --extra dev mypy` passes on `src/manta_trading` per the new `[tool.mypy]` block. Latent-bug fixes (F821 class) get their own commit(s) with a note distinguishing them from mechanical style fixes. Verifiable: `uv run --extra dev ruff check src/ test/` reports zero violations; mypy per the config block passes; all per-subpackage test suites pass unchanged; the F821 code paths are exercised by at least a smoke test or documented as dead code and removed. Dependencies: [166]. Risk: Med (BLE and F821 triage touches many files; behavior must be preserved except documented latent-bug fixes). Effort: 2/5

6. [ ] **(906) `mt data` CLI Module Decomposition** — `cli/commands/data.py` is 3,371 lines, >10x the ~300-line guideline, because eight Typer sub-apps (`data` root, `caggs`, `ca`, `instruments`, `lists`, `daemon`, `migrate`, `calendars`) were appended to one module rather than each getting its own. Flagged as F009 in the slice 163 review and deferred; slice 168's review established that over-guideline length is only excusable when the excess is not code — `data.py`'s excess is executable code, so it isn't. Split into a `cli/commands/data/` package, one module per sub-app plus `_shared.py` for the cross-group helpers, with `__init__.py` assembling the Typer tree and re-exporting `data_app` so `cli/app.py` is untouched. Generalizes the existing `universes.py` precedent, which already lives outside the module and is attached via `add_typer`. Strictly behavior-preserving: no signature, help-text, output-format, or exit-code changes, verified by diffing pre/post `--help` captures for all nine sub-apps. Pushing SQL and formatting logic down out of the oversized command bodies (`data_pull` 244 lines, `caggs_status` 202, `data_get` 192) is explicitly follow-on work, not this slice. Depends on 905 so that slice's `F821` latent-`NameError` findings in this exact file are fixed while the code is still in one place, rather than scattered across ten new modules mid-triage. Dependencies: [905]. Risk: Low (mechanical move; live-daemon entry point `daemon run` requires a real prod invocation to verify). Effort: 2/5

7. [ ] **(907) CI Pipeline and Load-Test Gating** — The repo has **no CI**; `.github/workflows` does not exist, so ruff, mypy, pytest, and the `test/load/` NFR tier all run only when someone remembers to locally. Slice 146 added two load tests gated on `MT_RUN_LOAD_TESTS=1` whose docstrings say "CI must enable" — nothing ever did, so those NFR assertions have never run automatically. Slice 167's task review (F002) caught the identical claim being made again for its sub-second `data_status` load test; the PM ruled the CI work out of 167 and into this chore rather than ship a second NFR with an aspirational gate. Scope: workflow config running ruff → mypy → pytest per-subpackage on push/PR, plus a load-tier job setting `MT_RUN_LOAD_TESTS=1` so the 146 and 167 NFRs get a real gate; decide per-suite whether DB-dependent tests get an ephemeral service container or a recorded skip; quarantine the known pre-existing failure baseline (2 failures + 12 live-DB errors) so a green run means something; retire the "CI must enable" docstrings. Must respect the broken whole-`test/` collection (missing `__init__.py` — per-subpackage invocation) and must never be able to reach the production DB at 192.168.1.144, where the daemon runs continuously. Explicitly excludes new tests, threshold changes, and fixing the baseline failures. Dependencies: [905]. Risk: Low (additive; no production code paths touched — the risk is a pipeline that goes red on day one and gets ignored, which the baseline quarantine addresses). Effort: 2/5

## Integration Work

8. [x] **(904) Packaging and Version** — Completed incrementally across slices 900–903: `uv.lock` committed, `mt` entry point wired, `mt --version` implemented, `pip install -e .` and `uv sync` verified. No standalone slice design needed. Dependencies: [903]. Effort: 0/5

## Notes

- Slice 900 is the largest because it establishes both CLI and config systems together — they're co-dependent (CLI needs config for setup, config needs CLI for management commands). Splitting them would create circular dependencies.
- Slice 903 carries medium risk because the deprecated code removal touches the main entry point and the httpx migration changes the HTTP layer. Both are well-scoped but affect working functionality.
- The daily pipeline integration (903) is intentionally minimal — wire existing working code into new CLI commands, don't redesign it. That's Initiative 100's job.
- Squadron (~/source/repos/manta/squadron/) is the reference implementation for CLI, config, logging, and provider patterns. Structural replication, not domain logic.
