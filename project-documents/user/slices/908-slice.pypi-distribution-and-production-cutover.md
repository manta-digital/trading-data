---
docType: slice-design
slice: pypi-distribution-and-production-cutover
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [904]
interfaces: [907, 909, 910, 911]
dateCreated: 20260801
dateUpdated: 20260801
status: not-started
---

# Slice Design: PyPI distribution and production cutover

## Overview

Slice 904 closed out "packaging" but delivered no *distribution*. Today the
only way to obtain `mt` is a source checkout, the only signal that a new
version exists is a bare git tag, and production installs by `git clone`.
Three consequences, each independently verified 2026-08-01:

1. **No install path.** [README.md:13](../../../README.md) documents
   `git clone` + `uv sync`. PyPI holds `manta-trading` at `0.0.1`
   ("Manta Trading - placeholder package"), a name reservation for a future
   full product — not this data package — while the repo is at 0.5.0.
2. **No notification path.** `v0.5.0` is tagged and pushed, but
   `gh release list` is empty, so the release is invisible to anyone not
   reading `git tag -l`. `mt --version` reads `importlib.metadata` and never
   consults a registry.
3. **Production cannot be upgraded by any package manager.**
   [production-deploy.md](../runbooks/production-deploy.md) Phase 4 clones into
   `/opt/manta-trading`, and both systemd units hard-code that checkout —
   `WorkingDirectory=/opt/manta-trading`,
   `ExecStart=/usr/local/bin/uv run mt data daemon run --minute`
   ([mt-minute-daemon.service.tmpl:11-14](../../../deploy/systemd/mt-minute-daemon.service.tmpl),
   same shape in the daily unit). That runbook is also first-install-only: it
   has no upgrade procedure at all, and its migration step still passes the
   `--db all` flag retired by the single-DB consolidation in slice 152.

.144 currently runs 0.4.0 against a repo at 0.5.0, and there is no written,
tested procedure to close that gap.

## Value

An operator — including the PM on .144 — can install a pinned version, learn
that a newer one exists, and upgrade to it by a documented procedure that has
been executed rather than described. This is also the precondition for slice
909 (`mt update`), which has nothing to check against until real releases
exist.

## Decisions

### D1 — Rename the distribution now; the import package later

Distribution name and import name are independent. This slice renames only the
distribution, `manta-trading` → `manta-trading-data` (verified available on
PyPI 2026-08-01, along with `manta-data`, `manta-market-data`, `mt-trading-data`).
The import package stays `manta_trading`; slice 911 renames it to
`manta_trading_data` separately.

Rationale: the PM's decision is to rename both, but the import rename touches
every module in `src/` and `test/` and would gate the first publish behind a
large mechanical change for zero user-visible benefit. Users see the
distribution name and `mt`; the import root is internal, so 911 ships as an
ordinary version bump that an installed user does not notice. 911 is also
better sequenced after 906 decomposes the 3,371-line `cli/commands/data.py`,
rather than renaming imports in a module about to be split.

### D2 — The distribution name becomes a constant, not a literal

[cli/app.py:36](../../../src/manta_trading/cli/app.py) calls
`importlib.metadata.version("manta-trading")`. A distribution-name literal in
code fails *silently* on rename — `PackageNotFoundError` is already caught
there, so `mt --version` would report a fallback rather than error. It moves to
a single named constant in `constants.py`, referenced everywhere, per the
project rule that a value used in lookups is defined once.

### D3 — Trusted publishing (OIDC), publish job only

Publishing runs from GitHub Actions via trusted publishing — no stored API
token — modelled on the working `squadron/.github/workflows/ci.yml`. The PM
configured the PyPI pending publisher on 2026-08-01: project
`manta-trading-data`, owner `manta-digital`, repository `trading-data`,
workflow `ci.yml`, no environment. **The workflow's `environment:` key must
stay absent to match**, or the OIDC claim will not validate.

This slice adds `.github/workflows/ci.yml` containing *only* the tag-gated
publish job. Slice 907 later adds the lint/type/test job to the same file and
makes publish depend on it. The split matters: 907 depends on 905, the
1,706-violation lint sweep, and releases must not wait on it. Naming the file
`ci.yml` now — rather than `publish.yml` — is what lets 907 extend one workflow
instead of introducing a second.

PyPI has no project-creation UI, and a pending publisher explicitly does *not*
reserve a name; the first successful upload is what claims `manta-trading-data`
and converts the pending publisher to a normal one.

### D4 — First published version is 0.6.0

A change of distribution identity is user-visible and deserves its own minor
version rather than riding along on 0.5.0 (which is already tagged, and whose
tag predates the workflow file — a tag-gated workflow can only publish from a
commit that contains it). 0.5.0 gets a GitHub Release created retroactively for
history; 0.6.0 is the first PyPI artifact.

### D5 — Production pins an exact version

.144 installs `uv tool install manta-trading-data==X.Y.Z`, pinned, never
floating. Production must not change version as a side effect of an unrelated
reinstall, and upgrades must be deliberate acts recorded in the runbook.

### D6 — Delivery is staged, with a checkpoint before .144 is touched

The publish half is low-risk and self-correcting: a bad artifact is fixed by
publishing another version. The cutover half stops data acquisition if it goes
wrong. They are therefore sequenced with a hard checkpoint — publish, then
verify a clean-environment install, and only then touch production. Both halves
are in this slice; the checkpoint is a phase boundary, not a separate slice.

### D7 — The cutover keeps the checkout as its rollback path

`/opt/manta-trading` and the current systemd units are **not** removed during
the cutover. Rollback is: stop the daemons, restore the previous unit files,
restart. This matters because 0.4.0 was never published under the new
distribution name, so there is no published artifact to roll *back* to —
the checkout is the only way back. Removal of the checkout is explicitly
deferred to a follow-up after the tool install has run in production
unattended.

### D8 — Installing or upgrading never migrates the database

Code upgrade and schema change stay separate, explicit operator actions. The
upgrade runbook orders them (`mt data migrate status` → review → apply) but the
install step itself never touches the database. Slice 910 generalizes this into
an enforced plan-then-confirm gate; this slice must not pre-empt it by wiring
migrations into any install path.

### D9 — TestPyPI is a non-blocking rehearsal

The workflow's TestPyPI step carries `continue-on-error: true` and
`skip-existing: true`, matching squadron. TestPyPI is a separate namespace
needing its own pending publisher; if that is not configured, the step fails
harmlessly and the real publish proceeds.

## Scope

**In:**

- `pyproject.toml` distribution rename; distribution-name constant replacing the
  literal at `cli/app.py:36`
- `.github/workflows/ci.yml` — publish job only, tag-gated, `id-token: write`
- First published release (0.6.0) via tag push; GitHub Releases for 0.5.0 and
  0.6.0, bodies from CHANGELOG
- Both systemd unit templates rewritten to invoke the tool-installed `mt` with
  no `WorkingDirectory` coupling; the absolute path is determined against the
  real service user during implementation, not assumed
- `production-deploy.md`: first-install phase rewritten to `uv tool install`;
  new upgrade procedure added; stale `--db all` flag corrected
- Executed cutover of .144 from 0.4.0 to 0.6.0

**Out:**

- Lint/type/test CI jobs (907), import-package rename (911), `mt update` (909),
  migration confirmation gates (910)
- Removing the `/opt/manta-trading` checkout (see D7)
- Any decision about the `manta-trading` placeholder on PyPI, which remains
  reserved for the future full product

## Success criteria

1. `uv tool install manta-trading-data` into a clean environment yields a
   working `mt` whose `--version` matches the published version.
2. `gh release list` shows v0.5.0 and v0.6.0.
3. A tag push publishes to PyPI with no stored credential anywhere in the repo
   or in GitHub secrets.
4. `mt --version` is driven by the distribution-name constant — verified by
   changing the constant and observing the lookup follow it, not by inspection.
5. .144 runs both daemons from the tool-installed entry point, with no
   `/opt/manta-trading` path on either `ExecStart` line, and
   `mt --version` on the host reports 0.6.0.
6. The upgrade procedure in the runbook was *executed* against .144 from the
   installed 0.4.0 — not written from theory.
7. Daemon acquisition resumes and is observed healthy after cutover (heartbeat
   and `acquisition_state` advancing), not merely "the process started".

## Risks

- **Production interruption.** The cutover stops both daemons. Prod backfill is
  continuous and standing PM guidance is not to interrupt it; the stop window
  must be agreed with the PM before Phase 6 begins, and kept to the restart
  itself.
- **Irreversible per version.** A PyPI upload cannot be replaced — a mistake
  costs a version number, not a rollback. This is the reason for D9's rehearsal
  and the D6 checkpoint.
- **Service-user path assumptions.** `uv tool install` places `mt` under the
  invoking user's home; the systemd units run as `$MANTA_TRADING_SERVICE_USER`.
  Install and unit file must agree, verified on the host rather than assumed.

## Cross-slice dependencies and interfaces

- **[904]** — depends on; established the packaging metadata this extends.
- **[907]** — extends `ci.yml` with lint/type/test and makes publish depend on
  it. 907 must not rename or replace the workflow file.
- **[909]** — depends on this slice for a published package to check against;
  will consume the distribution-name constant from D2.
- **[910]** — the enforced version of D8. May land before this slice, which
  would put the confirmation gate in place before the first real upgrade.
- **[911]** — completes the rename decided in D1.

## Notes

- Verified 2026-08-01: `manta-trading` on PyPI is version 0.0.1, summary
  "Manta Trading - placeholder package". `manta-trading-data`,
  `manta-data`, `manta-market-data`, and `mt-trading-data` all return 404
  (unclaimed).
- `mt data migrate status` and `mt data migrate apply` both exist
  ([cli/commands/data.py:177](../../../src/manta_trading/cli/commands/data.py),
  `:204`); only the runbook's `--db all` flag is stale. `mt data init` shares
  the same apply path and is idempotent.
- The repo has no `.github/` directory at all as of this design.
