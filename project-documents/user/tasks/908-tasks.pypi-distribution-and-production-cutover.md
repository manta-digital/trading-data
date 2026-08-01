---
docType: tasks
slice: pypi-distribution-and-production-cutover
project: trading-data
lldReference: project-documents/user/slices/908-slice.pypi-distribution-and-production-cutover.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [904]
interfaces: [907, 909, 910, 911]
dateCreated: 20260801
dateUpdated: 20260801
status: not_started
---

# Tasks: PyPI distribution and production cutover

## Context summary

`mt` has no install path but a source checkout, no notification path for new
versions, and production (.144) installs by `git clone` with both systemd units
hard-coding that checkout — so no package manager can upgrade it. .144 runs
0.4.0 against a repo at 0.5.0 with no written procedure to close the gap.

This slice publishes `manta-trading-data` to PyPI via trusted publishing, adds
GitHub Releases, and cuts .144 over to a pinned `uv tool install`.

### What already exists (verified in tree 2026-08-01)

- `pyproject.toml`: `name = "manta-trading"`, `version = "0.5.0"`,
  `[project.scripts] mt = "manta_trading.cli.app:app"`.
- `cli/app.py:36-38`: `importlib.metadata.version("manta-trading")` literal,
  falling back to `"dev"` on `PackageNotFoundError`.
- `config/manager.py:18,23`: hard-coded `~/.config/manta-trading/config.toml`
  and `.manta-trading.toml`.
- `deploy/systemd/mt-{daily,minute}-daemon.service.tmpl`:
  `WorkingDirectory=/opt/manta-trading`,
  `ExecStart=/usr/local/bin/uv run mt data daemon run --{daily,minute}`.
- `runbooks/production-deploy.md`: first-install only; Phase 4 is `git clone` +
  `uv sync`; line 147 still passes the retired `--db all` flag.
- **No `.github/` directory at all.**
- PM configured the PyPI pending publisher 2026-08-01: `manta-trading-data` /
  `manta-digital` / `trading-data` / `ci.yml` / no environment. TestPyPI
  publisher likewise.
- Reference workflow: `~/source/repos/manta/squadron/.github/workflows/ci.yml`.

### Non-negotiables from the design

- The workflow must have **no `environment:` key** — it would break the OIDC
  claim against a publisher configured with a blank environment (D3).
- Config path *values* never change; only their definition location (D10).
- The import package stays `manta_trading`; the entry point target is untouched
  (D1). Slice 911 renames it.
- No install or upgrade step may apply database migrations (D8).
- `/opt/manta-trading` and the current unit files are **not** deleted — they are
  the only rollback path, since 0.4.0 was never published under the new name (D7).
- Task 5 is a hard checkpoint. Do not begin Task 6 without PM confirmation and
  an agreed daemon stop window (D6).

---

## Task 1 — Constants (D2, D10)

- [ ] **1.1 Add `DISTRIBUTION_NAME` to `constants.py`**
  - [ ] Value `"manta-trading-data"`, typed `str`, module-level `Final`.
  - [ ] Docstring states this is the *distribution* name only: the import
        package is `manta_trading` (911 renames it) and the config paths in 1.2
        deliberately do not track it (D10).
  - Success: importable; no distribution-name literal remains outside this file.
  - Effort: 1

- [ ] **1.2 Add config path constants to `constants.py`**
  - [ ] `USER_CONFIG_DIR_NAME = "manta-trading"` and
        `PROJECT_CONFIG_FILENAME = ".manta-trading.toml"`, both `Final`.
  - [ ] Docstring states the values intentionally retain `manta-trading` and are
        keyed to the `mt` product identity, not the distribution — changing them
        would orphan existing user config. Cite D10 so a future rename cannot
        drag them along.
  - Success: importable; values byte-identical to today's literals.
  - Effort: 1

- [ ] **1.3 Wire `cli/app.py` version callback to the constant**
  - [ ] Replace the `"manta-trading"` literal with `DISTRIBUTION_NAME`.
  - [ ] In the `PackageNotFoundError` branch, keep `version = "dev"` but add a
        `logger.warning` naming the distribution string that was looked up, so
        "not installed" and "constant is wrong" are distinguishable (D2, F003).
  - Success: `mt --version` unchanged in a source checkout, and the warning
        appears in its output stream when metadata is absent.
  - Effort: 1

- [ ] **1.4 Wire `config/manager.py` to the path constants**
  - [ ] Both functions build their paths from the 1.2 constants.
  - Success: `mt config path` output is byte-identical to before the change.
  - Effort: 1

- [ ] **1.5 Unit tests for 1.3 and 1.4**
  - [ ] Version callback: with metadata present, reports the resolved version;
        with `PackageNotFoundError` raised, reports `dev` **and** emits the
        warning (assert on the log record, not just the output).
  - [ ] Config paths: assert the exact expected paths, so a future rename that
        moves them fails a test rather than silently relocating user config.
  - Success: tests pass; `uv run --extra dev mypy` clean on touched files.
  - Effort: 2

- [ ] **Commit**: `refactor: lift distribution name and config paths to constants`

---

## Task 2 — Distribution rename (D1, D4)

- [ ] **2.1 Rename the distribution in `pyproject.toml`**
  - [ ] `name = "manta-trading-data"`, `version = "0.6.0"`.
  - [ ] Leave `[project.scripts] mt = "manta_trading.cli.app:app"` untouched —
        the import package is unchanged in this slice.
  - Success: file parses; `uv build` names artifacts `manta_trading_data-0.6.0`.
  - Effort: 1

- [ ] **2.2 Regenerate `uv.lock`**
  - [ ] `uv lock` — the lockfile records the project name and will not match a
        renamed distribution otherwise. Commit the regenerated lockfile.
  - Success: `uv sync` succeeds from clean; the lock's project entry reads
        `manta-trading-data`.
  - Effort: 1

- [ ] **2.3 Verify the local build and entry point**
  - [ ] `uv build`; inspect the wheel actually contains the `manta_trading`
        package and a `mt` console script.
  - [ ] `uv sync && uv run mt --version` reports `0.6.0` — this is the real test
        of Task 1.3, since metadata is now present under the new name.
  - Success: version reported is `0.6.0`, not `dev` (a `dev` here means the
        constant does not match the distribution name).
  - Effort: 2

- [ ] **2.4 Run the per-subpackage test suites**
  - [ ] Whole-`test/` collection is broken (missing `__init__.py`) — invoke per
        subpackage. Compare against the known baseline: `test_daily.py::
        TestRunDailyCycleFailurePaths::test_4xx_non_429_propagates` already
        fails on `main`.
  - Success: no failures beyond the recorded baseline.
  - Effort: 1

- [ ] **Commit**: `package: rename distribution to manta-trading-data, bump 0.6.0`

---

## Task 3 — CHANGELOG

- [ ] **3.1 Add the 0.6.0 entry**
  - [ ] Note the distribution rename, the new install method, and explicitly
        that the import package and config paths are unchanged, so a reader
        upgrading knows what does and does not move.
  - Success: entry present and dated; becomes the GitHub Release body in 5.3.
  - Effort: 1

- [ ] **Commit**: `docs: add 0.6.0 changelog entry`

---

## Task 4 — Publish workflow (D3, D9)

- [ ] **4.1 Create `.github/workflows/ci.yml`**
  - [ ] Publish job only — no lint/type/test job; that is slice 907's, and
        gating releases on it would put them behind 905's lint sweep.
  - [ ] Trigger on tag push matching `v*`; job guarded by
        `if: startsWith(github.ref, 'refs/tags/v')`.
  - [ ] `permissions: id-token: write` at job level (required for OIDC).
  - [ ] Steps: checkout → `astral-sh/setup-uv` → `uv python install 3.12` →
        `uv sync` → `uv build` → `pypa/gh-action-pypi-publish` against TestPyPI
        with `continue-on-error: true`, `skip-existing: true`,
        `attestations: false` → the same action against PyPI with no
        `repository-url`.
  - [ ] **No `environment:` key anywhere in the job** (D3).
  - Success: `actionlint` or a GitHub dry-run parse reports no errors; the file
        is named `ci.yml` so 907 extends it rather than adding a second workflow.
  - Effort: 2

- [ ] **4.2 Desk-check the workflow against the publisher config**
  - [ ] Confirm each field the OIDC claim carries matches what the PM registered:
        owner `manta-digital`, repo `trading-data`, workflow filename `ci.yml`,
        environment absent on both sides.
  - Success: a written field-by-field comparison in the commit message or task
        notes — a mismatch here fails at publish time with an opaque error.
  - Effort: 1

- [ ] **Commit**: `chore: add tag-gated PyPI publish workflow`

---

## Task 5 — CHECKPOINT A: publish and verify (D6)

**Do not proceed past this task without PM confirmation.**

- [ ] **5.1 Merge the branch and tag**
  - [ ] Merge to `main` (`--no-ff`). The tag must point at a commit that
        *contains* `ci.yml` — a tag-gated workflow cannot run from a commit
        predating it, which is why v0.5.0 could not be used (D4).
  - [ ] `git tag -a v0.6.0`, push the tag.
  - Success: tag pushed; the workflow appears in the Actions tab.
  - Effort: 1

- [ ] **5.2 Observe the publish**
  - [ ] TestPyPI step may fail — it is `continue-on-error` and its failure is
        not a blocker (D9).
  - [ ] The PyPI step must succeed. On failure, classify against D11 before
        retrying anything: an OIDC rejection consumes no version and is fixed by
        realigning the workflow; a completed upload can never be replaced, only
        superseded by a new patch version.
  - Success: `manta-trading-data` 0.6.0 is live on PyPI and the pending
        publisher has converted to a normal one.
  - Effort: 2

- [ ] **5.3 Create the GitHub Releases**
  - [ ] `gh release create v0.6.0` with the CHANGELOG 0.6.0 section as the body.
  - [ ] `gh release create v0.5.0` retroactively, so history is complete.
  - Success: `gh release list` shows both (success criterion 2). A tag with no
        release is the visible signal that a workflow did not fire (D11).
  - Effort: 1

- [ ] **5.4 Clean-environment install verification (success criterion 1)**
  - [ ] `uv tool install manta-trading-data` in an environment with no checkout
        on `PATH`; run `mt --version`.
  - [ ] Do **not** attempt this against TestPyPI — it does not mirror PyPI and
        dependency resolution will fail there regardless of artifact quality.
  - Success: `mt --version` reports `0.6.0`. If it reports `dev`, the
        distribution-name constant is wrong — stop and fix before Task 6.
  - Effort: 2

- [ ] **5.5 STOP — report to PM**
  - [ ] Report published version, release URLs, and clean-install result.
  - [ ] Obtain the agreed daemon stop window before Task 6. Prod backfill runs
        continuously and standing guidance is not to interrupt it.
  - Effort: 1

---

## Task 6 — systemd unit templates

- [ ] **6.1 Determine the real `mt` path on .144 — verify, do not assume**
  - [ ] Establish where `uv tool install` places the binary for the account the
        units run as (`$MANTA_TRADING_SERVICE_USER`), which may be a system
        account with a nonstandard or absent home.
  - [ ] Decide between installing as the service user (binary under its home)
        or installing with an explicit tool bin directory on a shared path.
        Record the decision and its reason — the units and the install command
        must agree, and this is the risk the design flagged.
  - Success: an absolute path that exists and is executable by the service user.
  - Effort: 2

- [ ] **6.2 Rewrite both unit templates**
  - [ ] `ExecStart` invokes the absolute tool-installed `mt` from 6.1; drop
        `uv run`.
  - [ ] Remove `WorkingDirectory=/opt/manta-trading` — the whole point is that
        the units no longer depend on a checkout.
  - [ ] Leave the `EnvironmentFile=/etc/manta-trading.env` line untouched;
        production configuration continues to come from there.
  - Success: neither template contains `/opt/manta-trading` or `uv run`.
  - Effort: 1

- [ ] **6.3 Validate the rendered units**
  - [ ] Render both via the existing `envsubst` step and run
        `systemd-analyze verify` on the results, as the deploy runbook already
        prescribes.
  - Success: no errors reported.
  - Effort: 1

- [ ] **Commit**: `chore: point systemd units at the installed mt entry point`

---

## Task 7 — Runbook and README

- [ ] **7.1 Rewrite the runbook's install phase**
  - [ ] Phase 4 becomes `uv tool install manta-trading-data==X.Y.Z` — pinned,
        never floating (D5).
  - [ ] State that `/opt/manta-trading` is no longer created for new installs.
  - Success: no `git clone` remains in the install path.
  - Effort: 2

- [ ] **7.2 Add the upgrade procedure the runbook has never had**
  - [ ] Ordered: stop daemons → `uv tool install --upgrade` (or install a pinned
        newer version) → `mt data migrate status` → **review the pending list**
        → apply → restart → verify.
  - [ ] State explicitly that installing never migrates (D8), and that the
        migration review step is deliberate rather than a formality — slice 910
        turns it into an enforced gate.
  - [ ] Include the rollback: restore the previous unit files and restart
        against the retained checkout (D7).
  - Success: the procedure is executable start to finish by someone who has not
        read this slice.
  - Effort: 2

- [ ] **7.3 Correct the stale migration flag**
  - [ ] Line 147's `mt data migrate apply --db all` drops `--db all`, retired by
        the single-DB consolidation in slice 152. The command itself is current.
  - Success: no `--db` flag remains in the runbook.
  - Effort: 1

- [ ] **7.4 Update the README installation section**
  - [ ] Lead with `uv tool install manta-trading-data`; keep the source checkout
        as a *development* setup, clearly labelled as such.
  - [ ] Note the distribution/import name distinction so `manta-trading-data`
        vs `manta_trading` does not read as an error.
  - Success: a new user can install without cloning.
  - Effort: 1

- [ ] **Commit**: `docs: add upgrade runbook and uv tool install instructions`

---

## Task 8 — Execute the cutover on .144

**Requires the PM-agreed stop window from 5.5.**

- [ ] **8.1 Pre-cutover capture**
  - [ ] Record current `mt --version`, the running unit files (copy them aside),
        and current `acquisition_state` / heartbeat values as the baseline that
        8.6 compares against.
  - Success: a written before-state; unit file backups exist on the host.
  - Effort: 1

- [ ] **8.2 Stop both daemons**
  - [ ] Stop daily and minute services. Keep the window to the restart itself.
  - Effort: 1

- [ ] **8.3 Install the pinned version**
  - [ ] `uv tool install manta-trading-data==0.6.0` per the 6.1 decision.
  - [ ] Verify `mt --version` on the host reports `0.6.0` **before** proceeding.
  - Success: reported version matches; a `dev` result means the install did not
        take and must be resolved before restart.
  - Effort: 1

- [ ] **8.4 Review and apply migrations as a separate, explicit step**
  - [ ] `mt data migrate status` first; read the pending list before applying
        anything. Do not fold this into the install (D8).
  - [ ] Apply only after review.
  - Success: migration state reports no pending entries afterward.
  - Effort: 2

- [ ] **8.5 Install the new units and restart**
  - [ ] Render and install both units from Task 6; `daemon-reload`; start.
  - [ ] Do **not** remove `/opt/manta-trading` (D7).
  - Success: both services active.
  - Effort: 1

- [ ] **8.6 Verify acquisition actually resumed (success criterion 7)**
  - [ ] Confirm the heartbeat and `acquisition_state` are *advancing* against
        the 8.1 baseline — "the process started" is not the criterion.
  - [ ] Watch for a period long enough to see real progress, and check journald
        for errors on both units.
  - Success: state advances; no unit errors.
  - Effort: 2

- [ ] **8.7 Record the executed procedure**
  - [ ] Fold any correction discovered during execution back into the Task 7
        runbook, so it documents what was actually done (success criterion 6).
  - Success: runbook and reality agree.
  - Effort: 1

- [ ] **Commit**: `docs: record executed production cutover to installed mt`

---

## Task 9 — Close-out

- [ ] **9.1 Verify every success criterion**
  - [ ] Walk criteria 1-7 from the design and record the evidence for each.
  - Effort: 1

- [ ] **9.2 Update slice and plan status**
  - [ ] Set the design doc status; check the 908 entry in
        `900-slices.foundation-cleanup.md`. Delegate checklist edits to
        `task-checker`.
  - Effort: 1

- [ ] **9.3 Note follow-ups**
  - [ ] Removal of the `/opt/manta-trading` checkout, deferred until the tool
        install has run unattended in production (D7).
  - [ ] Claiming `manta-trading` on TestPyPI, deferred by PM.
  - Effort: 1

---

## Notes

- The `manta-trading` 0.0.1 placeholder on PyPI is untouched by this slice and
  remains reserved for a future full product.
- TestPyPI account identity differs (`manta9000`, not `manta`); irrelevant to
  trusted publishing, which authenticates the repository via OIDC.
- Task 5.4's `dev` result is the single highest-signal failure in this slice: it
  means the constant and the distribution name disagree, and it would otherwise
  surface first on production.
