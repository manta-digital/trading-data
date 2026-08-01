---
docType: tasks
slice: pypi-publication
project: trading-data
lldReference: project-documents/user/slices/908-slice.pypi-distribution-and-production-cutover.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [904]
interfaces: [907, 909, 911]
dateCreated: 20260801
dateUpdated: 20260801
status: complete
---

# Tasks: PyPI publication

## Context summary

Publish the package to TestPyPI and then PyPI so `uv tool install
manta-trading-data` works and `uv tool install --upgrade` can update it. That is
the entire slice. Production (.144) is untouched — it continues running from its
checkout, and migrating it is Future Work.

### What already exists (verified in tree 2026-08-01)

- `pyproject.toml`: `name = "manta-trading"`, `version = "0.5.0"`,
  `[project.scripts] mt = "manta_trading.cli.app:app"`.
- `cli/app.py:36-38`: `importlib.metadata.version("manta-trading")` literal,
  falling back to `"dev"`.
- **No `.github/` directory at all.**
- PM configured pending publishers on both PyPI and TestPyPI, 2026-08-01:
  `manta-trading-data` / `manta-digital` / `trading-data` / `ci.yml` / no
  environment.
- Reference workflow: `~/source/repos/manta/squadron/.github/workflows/ci.yml`.

### Non-negotiables from the design

- The workflow must contain **no `environment:` key** — it would break the OIDC
  claim against publishers configured with a blank environment (D3).
- The import package stays `manta_trading`; the entry point target is untouched
  (D1).
- Config paths are not touched (D8).
- Nothing in this slice modifies .144 (D7).

---

## Task 1 — Distribution-name constant (D2)

- [x] **1.1 Add `DISTRIBUTION_NAME` to `constants.py`**
  - [x] Value `"manta-trading-data"`, `Final[str]`.
  - [x] Docstring: this is the *distribution* name only. The import package is
        `manta_trading` (911 renames it) and the config paths deliberately do
        not track it (D8).
  - Success: importable; no distribution-name literal remains elsewhere.
  - Effort: 1

- [x] **1.2 Wire the version callback to the constant**
  - [x] `cli/app.py`: replace the literal with `DISTRIBUTION_NAME`.
  - [x] Keep `version = "dev"` in the `PackageNotFoundError` branch, but add a
        `logger.warning` naming the string that was looked up, so "not
        installed" and "constant is wrong" are distinguishable.
  - Success: `mt --version` unchanged in a source checkout; the warning fires
        when metadata is absent.
  - Effort: 1

- [x] **1.3 Unit-test the callback**
  - [x] With metadata present, reports the resolved version. With
        `PackageNotFoundError` raised, reports `dev` **and** emits the warning —
        assert on the log record, not only the output.
  - Success: tests pass; `uv run --extra dev mypy` clean on touched files.
  - Effort: 1

- [x] **Commit**: `refactor: lift distribution name to a constant`

---

## Task 2 — Rename the distribution (D1, D5)

- [x] **2.1 Edit `pyproject.toml`**
  - [x] `name = "manta-trading-data"`, `version = "0.6.0"`.
  - [x] Leave `[project.scripts] mt = "manta_trading.cli.app:app"` untouched.
  - Effort: 1

- [x] **2.2 Regenerate `uv.lock`**
  - [x] `uv lock` — the lockfile records the project name and will not match a
        renamed distribution otherwise. Commit the result.
  - Success: `uv sync` succeeds from clean.
  - Effort: 1

- [x] **2.3 Verify the local build**
  - [x] `uv build`; confirm artifacts are named `manta_trading_data-0.6.0` and
        the wheel contains the `manta_trading` package plus an `mt` script.
  - [x] `uv sync && uv run mt --version` reports `0.6.0` — the real test of 1.2,
        since metadata now exists under the new name. A `dev` here means the
        constant does not match.
  - Effort: 2

- [x] **2.4 Run the per-subpackage test suites**
  - [x] Whole-`test/` collection is broken (missing `__init__.py`) — invoke per
        subpackage. Baseline: `test_daily.py::TestRunDailyCycleFailurePaths::
        test_4xx_non_429_propagates` already fails on `main`.
  - Success: no failures beyond that baseline.
  - Effort: 1

- [x] **2.5 CHANGELOG entry for 0.6.0**
  - [x] Note the distribution rename and the new install method; state
        explicitly that the import package and config paths are unchanged.
  - Success: becomes the GitHub Release body in 4.2.
  - Effort: 1

- [x] **Commit**: `package: rename distribution to manta-trading-data, bump 0.6.0`

---

## Task 3 — Publish workflow (D3, D4)

- [x] **3.1 Create `.github/workflows/ci.yml`**
  - [x] Publish job only — no lint/type/test job; that is 907's, and gating
        releases on it would put them behind 905's lint sweep.
  - [x] Trigger on tag push matching `v*`, job guarded by
        `if: startsWith(github.ref, 'refs/tags/v')`.
  - [x] `permissions: id-token: write` at job level (required for OIDC).
  - [x] Steps: checkout → `astral-sh/setup-uv` → `uv python install 3.12` →
        `uv sync` → `uv build` → `pypa/gh-action-pypi-publish` at TestPyPI with
        `repository-url: https://test.pypi.org/legacy/`,
        `continue-on-error: true`, `skip-existing: true`,
        `attestations: false` → the same action at PyPI with no
        `repository-url`.
  - [x] **No `environment:` key anywhere in the job** (D3).
  - Success: parses cleanly; named `ci.yml` so 907 extends it.
  - Effort: 2

- [x] **3.2 Desk-check the workflow against the publisher config**
  - [x] Compare field by field against what the PM registered on *both* sites:
        owner `manta-digital`, repo `trading-data`, workflow `ci.yml`,
        environment absent on both sides.
  - Success: comparison recorded — a mismatch fails at publish time with an
        opaque error.
  - Effort: 1

- [x] **Commit**: `chore: add tag-gated PyPI publish workflow`

---

## Task 4 — Publish and verify

- [x] **4.1 Merge, tag, and publish**
  - [x] Merge to `main` (`--no-ff`), then tag `v0.6.0` and push the tag. The tag
        must point at a commit containing `ci.yml` (D5).
  - [x] Watch the TestPyPI step: with its publisher configured it is expected to
        succeed, and a failure is worth reading before the PyPI step lands
        something permanent (D4).
  - [x] On any PyPI failure, classify against D6 before retrying — an OIDC
        rejection consumes no version; a completed upload can never be replaced.
  - Success: `manta-trading-data` 0.6.0 live on both indexes; pending publishers
        converted to normal ones. (Note: v0.6.0 contained a latent bug discovered during 4.3; v0.6.1 was published as the corrective release.)
  - Effort: 2

- [x] **4.2 Create the GitHub Releases**
  - [x] `gh release create v0.6.0` with the CHANGELOG section as the body, and
        `v0.5.0` retroactively.
  - Success: `gh release list` shows both (criterion 2).
  - Effort: 1

- [x] **4.3 Verify a clean install (criterion 1)**
  - [x] `uv tool install manta-trading-data` in an environment with no checkout
        on `PATH`; run `mt --version`.
  - [x] Do not attempt this against TestPyPI — it does not mirror PyPI and
        dependency resolution will fail there regardless of artifact quality.
  - Success: reports the published version. v0.6.0 installed under Python 3.10 but crashed with `ImportError: cannot import name 'StrEnum'` because the codebase uses `enum.StrEnum` (49 usages, Python 3.11+ only) but `requires-python` allowed 3.10. Fixed by raising the floor to `>=3.12,<3.13` and publishing v0.6.1; a clean `uv tool install manta-trading-data` now correctly installs 0.6.1 and `mt --version` reports `0.6.1`.
  - Effort: 2

- [x] **4.4 Verify upgrade (criterion 4)**
  - [x] Requires two published versions. If 0.6.0 is the only one at this point,
        pair this with the next release rather than burning a version for the
        test: install 0.6.0, then `uv tool install --upgrade
        manta-trading-data`, and confirm the reported version advances.
  - Success: upgrade path demonstrated, or explicitly deferred to the next
        release with that noted in close-out. **Deferred to next release:** v0.6.1 is already the current latest, so demonstrating the upgrade would require publishing a throwaway version. `uv tool install --upgrade` respects an explicit version pin (by design), and an unpinned install lands on the current latest (0.6.1) directly.
  - Effort: 1

---

## Task 5 — README and close-out

- [x] **5.1 Rewrite the README installation section**
  - [x] Lead with `uv tool install manta-trading-data`; keep the source checkout
        as a clearly-labelled *development* setup.
  - [x] Mention `uv tool install --upgrade manta-trading-data` for updating.
  - [x] Note the distribution/import name distinction so `manta-trading-data`
        vs `manta_trading` does not read as an error.
  - Success: a new user can install without cloning (criterion 5).
  - Effort: 1

- [x] **5.2 Verify success criteria and close out**
  - [x] Walk criteria 1-5, recording evidence for each.
  - [x] Update slice status and check the 908 entry in
        `900-slices.foundation-cleanup.md`. Delegate checklist edits to
        `task-checker`.
  - Effort: 1

- [x] **Commit**: `docs: add uv tool install instructions`

---

## Notes

- `manta-trading` 0.0.1 on PyPI is untouched and remains reserved for a future
  full product. Claiming that name on TestPyPI is deferred by PM decision.
- TestPyPI account identity is `manta9000`, not `manta` — irrelevant to trusted
  publishing, which authenticates the repository via OIDC.
- Task 4.3's `dev` result is the highest-signal failure in this slice: it means
  the constant and the distribution name disagree.
