---
docType: slice-design
slice: tick-archive-adoption
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [923, 220, 221, 222]
interfaces: [224, 225, 227]
dateCreated: 20260928
dateUpdated: 20260928
status: not_started
---

# Slice Design: tick-archive-adoption

## Overview

Slice 223 is the first half of the historical acquisition pass design,
split out on 2026-09-28 (PM direction) so each half fits one implementation
session. **The design of record is
`user/slices/224-slice.historical-acquisition-pass.md`** (the LLD below). It
was designed and reviewed as one document, and its "Slice Split" section
assigns each part to 223 or 224. This document states 223's scope, its
success criteria and its walkthrough, and cites the LLD for every decision.
It adds no decision of its own.

223 spends no money. When it is done:

- the two free-credit ES jobs are copied into `/data/tick-archive`, verified
  file by file and day by day, and recorded as adopted purchases in the tick
  manifest;
- the archive is in the nightly restic backup, with a one-file restore
  proven;
- exhausted and holed units can be reset by hand.

Tier units rest at *verified*. Their definitions are bought by 224.

## Technical Scope

Each item cites the LLD's Technical Decision (TD) that governs it.

1. **Settings:** `MT_TICK_SPEND_30D_CEILING_USD` and `MT_TICK_ARCHIVE_DIR`
   (LLD, API Contracts, Settings). The 30-day ceiling is only stored here;
   224's spend guard reads it.
2. **Constants** used by this slice: lock key, connect timeout, env names
   (LLD, Patterns and Conventions).
3. **Migration `tick_006_availability`** and its two grants (LLD, Database /
   Storage Schema). The availability tables are created here and written by
   224.
4. **Session days**, `data/tick/session_days.py` (TD4, Days). Adoption needs
   them to create one unit per session day.
5. **Run context**: the preflight, including the unknown `MT_TICK_*` key
   refusal, and the advisory lock (TD2).
6. **Manifest repository**, compare-and-set transitions for adoption,
   verification, failure recording and reset (TD2; LLD State Management).
7. **Verification**, `data/tick/verify.py` (TD9, Verification).
8. **Adoption**, `mt data tick adopt` (TD10), reading the calendar from the
   production database (TD6).
9. **Reset**, `mt data tick reset` (TD8, reset).
10. **Archive backup enrolment**: the include list, the `.partial`
    exclusion, `scripts/verify_tick_archive_backup.sh` and runbook 200
    (TD11).
11. **Documentation** for the above: the contract rows below, README,
    `.env_sample`, migrations README and CHANGELOG.

**Excluded (224):** the pass contract, the universe, the planner, the spend
guard, availability capture, submit and delivery, definitions projection,
and `mt data tick pass`.

## Dependencies

222 (complete), 220 (complete), 221 (complete), 923 (complete), and the
production database's CME calendar (read only). The LLD's Dependencies
section applies unchanged, including the note that no slice yet provisions
the production tick cluster: 223 runs on a scratch tick database, and the
archive can rebuild the manifest (TD10).

## Success Criteria

From the LLD's Functional Requirements (FR):

- **FR1, preflight:** `adopt` and `reset` exit 1 naming the variable or
  command for each refusal in TD2.
- **FR2, adoption:** both free-credit jobs adopted (26 and 52 units
  *verified*, no holes), re-adoption a no-op, a corrupted byte refuses the
  whole job with no rows.
- **FR6, reset part:** reset of a hole reopens it; reset of an exhausted
  unit sets `UNKNOWN` with `attempt_count = 0`.
- **Backup:** the verify script's log shows the snapshot's archive file
  count equal to the archive's, and a restored file's SHA-256 equal to the
  original's.
- The LLD's Technical Requirements apply to this slice's code: unit and
  integration tests, ruff and mypy clean, files under about 300 lines, no
  paid method reachable from any test.

**Walkthrough:** the LLD's Verification Walkthrough steps 1–4, 8 and 9. The
scratch database `mt_scratch_tick_223` is kept for 224's steps 5–7 and 10.

## Standing obligations

- **Contract rows:** I9 (loud preflight refusals and the lock), I10 (the
  `adopt` and `reset` verbs), I11 (the manifest writes and the
  availability tables' creation).
- **Kalshi contract diff:** not applicable; the contract copy is 224's.
- **Realtime paths:** adoption and reset rule out neither path; the LLD's
  Special Considerations apply.
- **Does this belong in the API?** No. Adoption and reset are operator
  actions on the manifest (LLD, API Contracts).

## Provides to 224

The run context, the lock, the compare-and-set repository, verification,
session days, `tick_006`, both settings, and a scratch manifest holding the
two adopted jobs.
