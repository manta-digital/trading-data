---
docType: slice-design
slice: pypi-publication
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [904]
interfaces: [907, 909, 911]
dateCreated: 20260801
dateUpdated: 20260801
status: not-started
---

# Slice Design: PyPI publication

## Overview

`mt` cannot be installed except by cloning the repo, and an installed copy has
no way to learn that a newer version exists. This slice publishes the package to
PyPI so that `uv tool install manta-trading-data` works, and to TestPyPI so a
release can be rehearsed before it is permanent.

That is the whole slice. It deliberately does **not** touch production.

## Value

Four capabilities, in the PM's words:

1. Install it easily, so `mt` works.
2. Know whether an update is available (`mt update` — slice 909, which cannot
   exist until something is published).
3. Update it: `uv tool install --upgrade manta-trading-data`.
4. Both of the above require publication to PyPI, and to TestPyPI first so the
   artifact can be tested before a version number is burned permanently.

## Decisions

### D1 — Rename the distribution; leave the import package alone

Distribution name and import name are independent. `manta-trading` on PyPI is a
0.0.1 placeholder reserved for a future full product, so this package publishes
as **`manta-trading-data`** (verified unclaimed on both PyPI and TestPyPI,
2026-08-01). The import package stays `manta_trading` and the `mt` entry point
target is untouched; slice 911 renames the import root separately, as an
ordinary version bump no installed user notices.

### D2 — The distribution name becomes a constant

[cli/app.py:36](../../../src/manta_trading/cli/app.py) calls
`importlib.metadata.version("manta-trading")`, catching `PackageNotFoundError`
and falling back to the literal `"dev"`. A distribution-name literal fails
*silently* on rename — an installed package would report `dev`. It moves to a
single constant, per the project rule that a value used in lookups is defined
once, and slice 909 consumes the same constant when querying the registry.

`"dev"` is retained: it is an obviously-placeholder value, which the project
rules permit, and it is the correct answer in a source checkout. But the except
branch gains a `logger.warning` naming the string it looked up, so "not
installed" and "the constant is wrong" stop being indistinguishable.

### D3 — Trusted publishing, publish job only

Publishing runs from GitHub Actions via OIDC trusted publishing — no stored
token — modelled on the working `squadron/.github/workflows/ci.yml`. The PM
configured both pending publishers on 2026-08-01: project `manta-trading-data`,
owner `manta-digital`, repository `trading-data`, workflow `ci.yml`, **no
environment**. The workflow must therefore contain no `environment:` key, or the
OIDC claim will not validate.

The new `.github/workflows/ci.yml` carries only the tag-gated publish job.
Slice 907 later adds lint/type/test to the same file; naming it `ci.yml` now is
what allows that rather than a second workflow. Releases must not be gated
behind 907, which itself waits on 905's lint sweep.

A pending publisher does not reserve a name — the first successful upload is
what claims `manta-trading-data` and converts the publisher to a normal one.

### D4 — TestPyPI first, and it is a real gate

The workflow publishes to TestPyPI before PyPI. Both pending publishers are
configured, so the TestPyPI step is expected to succeed and its failure is
worth looking at — this is the rehearsal that makes a permanent PyPI version
safe to burn.

It carries `skip-existing: true` so re-runs of a tag are not errors, and
`continue-on-error: true` so a TestPyPI outage cannot block a real release.
TestPyPI is a rehearsal for the *artifact*, not for installing: it does not
mirror PyPI, so most dependencies are absent there and a plain
`uv tool install` from it will fail on resolution regardless of artifact
quality. Install verification happens against real PyPI.

### D5 — First published version is 0.6.0

A change of distribution identity deserves its own minor version. It also has to
be a new tag regardless: the workflow is tag-gated and can only publish from a
commit that contains it, which v0.5.0's tag predates. v0.5.0 gets a GitHub
Release retroactively so history is complete; 0.6.0 is the first PyPI artifact.

### D6 — Publish failure modes

- **OIDC claim rejected** (an `environment:` key added, workflow renamed). Fails
  loudly, uploads nothing, consumes no version. Fix the workflow and re-tag.
- **Name collision.** A pending publisher reserves nothing, so the name could in
  principle be taken between the 2026-08-01 check and the first push. The
  publisher is invalidated and a different name must be chosen — D2's constant
  is what makes that a one-line change. No step may assume the name is held
  before the first successful upload.
- **Interrupted upload.** No version is consumed unless the upload completed;
  `skip-existing` covers a retry. A version that *did* land can never be
  replaced, only superseded by a new patch version.
- **Workflow does not fire** (tag pattern mismatch, Actions disabled). The
  silent case, and why success criterion 2 checks `gh release list` rather than
  trusting the tag: a tag with no release is the visible signal.

### D7 — Production is out of scope, and unaffected

.144 continues to run from its `/opt/manta-trading` checkout exactly as it does
today. Nothing in this slice modifies the host, the systemd units, the service
account, or the deploy runbook. Migrating production onto a published artifact
is recorded as Future Work in the slice plan, to be designed on its own terms
when it is wanted — the earlier attempt to fold it in here forced an install
mechanism designed for interactive user tools onto a `nologin` system account,
which produced complexity out of proportion to the goal.

### D8 — Config paths are unaffected

The architecture mandates user config at `~/.config/manta-trading/config.toml`
and project config at `.manta-trading.toml`, currently literals at
[config/manager.py:18,23](../../../src/manta_trading/config/manager.py). They
are keyed to the `mt` product identity, not to the distribution name, and they
hold user data — a rename would silently orphan existing config for no benefit.
**They do not change, and this slice does not touch them.** Lifting them to
constants is recorded as Future Work; slice 911 must not move them either.

## Scope

**In:** distribution rename and version bump in `pyproject.toml`; regenerated
`uv.lock`; distribution-name constant and the `mt --version` warning;
`.github/workflows/ci.yml` publish job; first publish to TestPyPI and PyPI;
GitHub Releases for v0.5.0 and v0.6.0; verified `uv tool install` from PyPI;
README install instructions.

**Out:** everything touching .144 (D7); config path refactor (D8); import
package rename (911); `mt update` (909); migration gating (910); lint/type/test
CI (907).

## Success criteria

1. `uv tool install manta-trading-data` in a clean environment yields a working
   `mt` whose `--version` reports the published version — not `dev`, which would
   mean the constant and the distribution name disagree.
2. `gh release list` shows v0.5.0 and v0.6.0.
3. A tag push publishes to TestPyPI and then PyPI with no credential stored in
   the repo or in GitHub secrets.
4. `uv tool install --upgrade manta-trading-data` is demonstrated between two
   published versions.
5. The README lets a new user install without cloning.

## Design review disposition (20260801)

Review: `user/reviews/908-review.slice.pypi-distribution-and-production-cutover.md`,
minimax/minimax-m3, verdict CONCERNS. All findings verified against source
before disposition. The slice was subsequently narrowed by PM direction to
publication only; the dispositions survive that narrowing.

- **F001 (concern) — config paths unaddressed.** Valid; confirmed at
  `config/manager.py:18,23`. Answered by **D8**: the paths do not change and the
  slice does not touch them. The constant-lifting the finding suggested is
  recorded as Future Work rather than done here.
- **F002 (note) — publish failure modes.** Valid. Answered by **D6**. The
  name-collision case changed the design: no step assumes the name is held
  before first upload.
- **F003 (note) — `dev` masks a wrong constant.** Confirmed at
  `cli/app.py:36-38`. Answered in **D2** by a warning that distinguishes "not
  installed" from "constant is wrong", with `"dev"` retained.
- **F004, F005, F006 (pass).** No action.

## Cross-slice dependencies and interfaces

- **[904]** — depends on; established the packaging metadata this extends.
- **[907]** — extends `ci.yml` with lint/type/test; must not rename or replace
  the workflow file.
- **[909]** — depends on this slice for something to check against; consumes
  D2's constant.
- **[911]** — completes the rename begun in D1, and inherits D8.

## Notes

- Verified 2026-08-01: `manta-trading-data` returns 404 on both pypi.org and
  test.pypi.org. `manta-trading` is 0.0.1 on PyPI, absent from TestPyPI.
- TestPyPI account identity differs (`manta9000`, not `manta`). Irrelevant to
  trusted publishing, which authenticates the repository via OIDC.
- The repo has no `.github/` directory as of this design.
