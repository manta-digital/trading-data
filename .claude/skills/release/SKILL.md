---
name: release
description: Cut a manta-trading release end to end — merge to main, bump the version, update the changelog, tag, push, watch CI, deploy to production, verify. Use when the user says "release", "cut a release", "ship it", or "bump and tag". The agent does every step; there are no human steps.
---

# Release manta-trading

Run it all yourself and report the result at the end. Never hand the user a step, a command list or a "say go": CLAUDE.md ("No human proxy steps") forbids it, and merge, tag, push and deploy are all yours. Stop only at a hard wall (a sudo prompt that cannot be satisfied, a red CI run) and name the exact command or log line.

## 1. Find the right place

1. `git worktree list`. Releases are cut on `main`. If `main` is checked out in another worktree, run every `main` step below with `git -C <that worktree>`; if it is checked out nowhere, `git checkout main` here. Never force-move a branch another worktree holds.
2. `git fetch origin`. Fast-forward `main` to `origin/main`.
3. Read the target with `cf config get git.integration_branch` (empty means `main`). If it names a branch with unreleased work, merge it into `main` with `git merge --ff-only`, or a plain merge if main has moved, and resolve conflicts. The standing PM instruction to release overrides the "never merge to main" rule.
4. The tree must be clean. If it is not, commit what belongs to the work (leave no stragglers) or stop and show `git status` if you cannot tell what it is.

## 2. Pick the version

Current version: `pyproject.toml`. Read `## [Unreleased]` in `CHANGELOG.md`; if it is empty, write the entries from `git log <last tag>..main` first (short, user-facing, referencing issue numbers).

- **Patch** is the default: fixes, refinements, new flags, options, parameters, config keys, checks, docs.
- **Minor** only for a new user-facing feature or a significant new AI capability. Decide it yourself and state the reason in one line; do not wait for confirmation.
- **Major** only when the user asks for it.

## 3. Bump and document

1. Set `version` in `pyproject.toml`, then `uv lock`. The lockfile entry for `manta-trading-data` must change; nothing else should.
2. In `CHANGELOG.md`, insert `## [X.Y.Z] - YYYY-MM-DD` directly under `## [Unreleased]`, so `[Unreleased]` is left empty.
3. Check, check-only (a format or lint failure stops the release; fix it in its own commit and start again): `uv run ruff check`, `uv run ruff format --check` on touched files, `uv run mypy src/manta_trading`, `uv run pytest test/unit`. Integration and load tiers are not part of the release gate.
4. Commit exactly `CHANGELOG.md`, `pyproject.toml` and `uv.lock`: `package: release X.Y.Z`.

## 4. Tag and push

```
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main vX.Y.Z
```

Invoking this skill is the request to push. If `main` is behind a shared integration branch, also fast-forward that branch to `main` and push it.

## 5. Watch CI

The workflow `.github/workflows/ci.yml` runs on the tag: build, publish to PyPI and TestPyPI, then create the GitHub release from the CHANGELOG section (`scripts/changelog_section.sh`).

`gh run list --workflow ci.yml --json databaseId,status,conclusion,headSha`, match `headSha` to the tagged commit, then `gh run watch <id> --exit-status`. On failure pull `gh run view <id> --log-failed`, report the real error and stop. On success confirm both:

- `gh release view vX.Y.Z`
- `curl -s -o /dev/null -w "%{http_code}" https://pypi.org/pypi/manta-trading-data/X.Y.Z/json` returns 200 (retry for a few minutes; PyPI can lag).

## 6. Deploy to production

The release is not done until production runs it.

1. List the migrations the release adds: compare the migration ids in `src/manta_trading/market/schema/migrations/` between the previous tag and this one (`git diff vPREV vX.Y.Z -- src/manta_trading/market/schema/migrations`).
2. **No new migration:** `uv run python scripts/upgrade_production.py vX.Y.Z`.
3. **New migrations:** run the cutover script for each, named in the CHANGELOG (`scripts/cutover_<issue-or-slice>_*.py vX.Y.Z`). A release that needs a migration and has no cutover script is missing work: write one on the pattern of `scripts/cutover_0244_symbol_names.py` (check-then-act, holds and releases the timers, verifies against the live API), commit it, and cut a patch release that includes it. Run cutovers in migration order; each is safe to re-run.
4. Run the scripts from the checkout of the release (or later, for script-only fixes). A script that fails on its own verify after the migration applied is a script bug: read the real error, fix the script, commit it, re-run. Do not re-apply by hand.
5. Confirm: `/opt/manta-trading/.venv/bin/mt --version` reports X.Y.Z, `systemctl is-active mt-serve.service` is `active`, and `curl -s http://127.0.0.1:8100/api/v1/health` returns `ok`.

## 7. Report

One short message: version, why that bump, release URL, PyPI status, which migrations and cutovers ran, and the production version check. Mention anything that needed a fix on the way.
