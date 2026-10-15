---
docType: tasks
slice: cross-source-acquisition-arbitration
project: trading-data
lld: user/slices/233-slice.cross-source-acquisition-arbitration.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [226]
interfaces: [234]
projectState: >
  Slice design committed and reviewed (CONCERNS, addressed). Prerequisites 226
  and 916 are complete. `manta-acquisition.slice` is a comment-only unit with
  no resource settings; three oneshot units (minute, daily, Kalshi) name it.
  Production runs `v0.19.0` and nothing in this slice is installed.
dateCreated: 20261014
dateUpdated: 20261014
status: not_started
---

# Tasks: Cross-Source Acquisition Arbitration

## Context Summary

- Working on slice 233. It sets `MemoryMax` on `manta-acquisition.slice`,
  derived from the journal's 30-day memory peaks, and records that calendar
  stagger stays as the arbitration mechanism. It closes 900 Future Work item 4.
- **No `CPUWeight` or `IOWeight`** (inert on this host: Technical Decision 1)
  and **no contention run** (Technical Decision 3). PM scope cut, 2026-10-14.
- It delivers:
  - `scripts/acquisition_memory_bound.py` (read-only derivation);
  - `deploy/systemd/manta-acquisition.slice` with `MemoryMax=` and its comment;
  - a drift guard in `test/unit/deploy/test_units.py`;
  - the runbook, 900 plan, architecture Revision Log and CHANGELOG entries.
- **Nothing is installed and no cutover script is built.** 234's cutover
  installs the file and verifies it is in force (issue #33). This slice claims
  only that the bound is declared and derived.
- **No change to `src/`.** The integration and load tiers are not required;
  the unit tier is.
- The slice plan's 233 entry and 234's #33 obligation were already written
  with the design; do not edit them.
- Next planned slice: 234 (Tick Pass Production Wiring).

**Conventions for every task below**

- Run from the repository root. Confirm with `pwd` before file or shell work.
- `ruff format` only the files the task touched. Before each commit, run
  `git diff main --stat` and look for deletions in files you did not mean to
  change (ruff rewrites whole files).
- mypy: one invocation covering the kalshi source paths plus tests, never a
  narrower run (project memory: narrower runs report false errors).
- Commit messages use the project's semantic prefixes and end with the
  attribution lines in force for the session.

---

## Section 0 — Baseline (read only)

- [ ] **0.1 Record host facts and capture journal lines**
  - [ ] Create `project-documents/user/notes/{YYYY-MM-DD}-233-findings.md`
        (today's date; `2026-08-23-916-verification-results.md` is the format
        model), with YAML front matter per `file-naming-conventions.md`.
  - [ ] Re-read, read-only, each row of Technical Decision 1's table (slice
        cgroup path, NVMe schedulers, presence of `/sys/fs/cgroup/io.cost.qos`,
        `free -g`, cgroups of the two PostgreSQL units) and record "matches
        design" or the difference. On any difference, **stop and tell the
        Project Manager**; the no-weights decision rests on these facts.
  - [ ] Per member (`mt-minute-pass`, `mt-daily-pass`, `mt-kalshi-pass`), run
        `journalctl -u <unit>.service -o cat --since -30d | grep 'memory peak'`
        and record the maximum peak.
  - [ ] Copy verbatim into the note one line of each form the parser must
        handle: a minute line with the `1h …min` wall-clock form and a `G`
        peak, a line with an `M` peak, and a line with no peak clause if any
        exists. **If the journal holds no line of a form, hand-write one of
        the same shape and mark it "synthetic" in the note and in the test
        fixture comment.** Do not skip the form.
  - [ ] Commit: `docs: record 233 baseline host facts and peaks`
  - [ ] Success: the note holds the table results, three maxima (minute about
        6.2 G) and the fixture lines; committed
  - [ ] Effort: 1

---

## Section 1 — Derivation script

The parser and the rule are pure functions; the journal read is the only I/O
and sits at the entry point (design: Component Structure).

- [ ] **1.1 Discovery, parser and rule, with their tests**
  - [ ] Create `scripts/acquisition_memory_bound.py` (docstring cites the 233
        design and states the script writes nothing). Named constants with
        docstrings citing the design: `MEMORY_BOUND_HEADROOM = 2`,
        `JOURNAL_WINDOW_DAYS = 30`, and the slice name (once, in this file).
  - [ ] Member discovery: unit names of `deploy/systemd/*.service` files that
        declare `Slice=manta-acquisition.slice`, parsed leniently, never a
        hand-written list.
  - [ ] Peak parser: bytes of the `<size> memory peak` clause wherever it
        appears in a `Consumed …` line (K/M/G/T suffixes, fractional values);
        `None` when absent. Do not anchor on what precedes or follows it.
  - [ ] Max-peak: the largest peak across lines; raises when none parse. No
        zero or default.
  - [ ] Rule: `ceil_GiB(MEMORY_BOUND_HEADROOM × Σ peaks)`; raises on an empty
        member set.
  - [ ] Create `test/unit/scripts/__init__.py` (empty) and
        `test/unit/scripts/test_acquisition_memory_bound.py` (import the script
        by putting `scripts/` on `sys.path`, as
        `test/unit/test_drill_228_fingerprint.py` does). Tests:
    - [ ] parser on the task 0.1 fixture lines (both forms, plus the line with
          no clause);
    - [ ] discovery against the real `deploy/systemd/` returns exactly the
          minute, daily and Kalshi services, and against a `tmp_path` excludes
          a unit with no `Slice=` or another slice;
    - [ ] max-peak raises on empty input and on lines with no peak;
    - [ ] rule: 6.2 G + 426 M + 317 M gives 14; an exact-GiB sum is not rounded
          up; an empty mapping raises.
  - [ ] Success: `uv run pytest test/unit/scripts -q` passes; functions < 50
        lines
  - [ ] Effort: 2

- [ ] **1.2 Entry point, with its tests**
  - [ ] `main()` reads each member's journal for the window, prints one line
        per member (name, peak), then the result in GiB, and writes no file.
        A journal read that fails raises with the unit and the command's
        stderr; nothing catches broadly. Exit `0` on success, `2` when a
        member's journal cannot be read or has no peak line (design: API
        Contracts); the process-boundary handler names the member on stderr
        and catches only the errors this script raises, with a comment saying
        why.
  - [ ] Keep the journal read separate from the pure functions so tests never
        call `journalctl`.
  - [ ] Tests (journal read monkeypatched with canned lines): exit 0 and
        output names all three members and the result; a member with no peak
        line gives exit 2 with the member named; a failing read gives exit 2;
        no file is created.
  - [ ] Success: `uv run pytest test/unit/scripts -q` passes; the script is
        < 300 lines; ruff clean on both files
  - [ ] Effort: 2

- [ ] **1.3 Run on the host and commit**
  - [ ] Run `uv run python scripts/acquisition_memory_bound.py` (walkthrough
        step 1). Expected: three members, minute about 6.2 G, about 14 GiB,
        exit 0. Paste the output into the findings note.
  - [ ] Scoped `ruff format` and mypy per the conventions above.
  - [ ] Commit: `feat: add acquisition memory-bound derivation script`
  - [ ] Success: output recorded; checks clean; committed
  - [ ] Effort: 1

---

## Section 2 — Unit file and drift guard

- [ ] **2.1 Set `MemoryMax` in the slice file**
  - [ ] In `deploy/systemd/manta-acquisition.slice`, replace the "Deliberately
        carries NO resource settings" comment with one that states: the rule
        and that the script computes it; the inputs and the date, from task
        1.3's output; that a unit joining the slice re-derives the value; and
        why `CPUWeight`/`IOWeight` are absent (no sibling cgroup under
        `manta.slice`, no I/O controller honouring weights on either NVMe
        device; cite the 233 design, Technical Decision 1).
  - [ ] Add `MemoryMax=<N>G` under `[Slice]`, `N` being the script's result.
  - [ ] Success: `systemd-analyze verify deploy/systemd/manta-acquisition.slice`
        prints nothing about `MemoryMax` (walkthrough step 3)
  - [ ] Effort: 1

- [ ] **2.2 Drift guard, and commit**
  - [ ] Add a `TestAcquisitionSlice` class to
        `test/unit/deploy/test_units.py`, using its existing `_parse` and
        `_SYSTEMD`. It stands alone and does not import the script. It asserts:
    - [ ] the slice file declares `MemoryMax` in `[Slice]` as `<integer>G`,
          integer > 0, and declares neither `CPUWeight` nor `IOWeight`;
    - [ ] at least one `*.service` names the slice, and every one that does is
          `Type=oneshot`.
  - [ ] Do not edit existing tests in the file.
  - [ ] Success: `uv run pytest test/unit/deploy/test_units.py test/unit/scripts/test_acquisition_memory_bound.py -q`
        passes (walkthrough step 2); deleting the `MemoryMax` line once makes
        the new class fail (restore it)
  - [ ] Commit: `feat: set MemoryMax on manta-acquisition.slice`
  - [ ] Effort: 2

---

## Section 3 — Documents

Locate each edit by grepping the quoted phrase or heading. If a phrase is not
found, stop and tell the Project Manager instead of editing a guessed place.

- [ ] **3.1 Runbook 100** (`user/runbooks/100-production-operations.md`)
  - [ ] Unit table: change "carries no resource limits yet" on the
        `manta-acquisition.slice` row to the bound and its source
        (`MemoryMax`, derived by `scripts/acquisition_memory_bound.py`),
        applied at the release that follows 234.
  - [ ] "Adding a source or pass": add a step before the install step. A unit
        that sets `Slice=manta-acquisition.slice` re-derives `MemoryMax` with
        the script and copies the result and inputs into the slice file's
        comment. Say the script exits 2 until the new unit has run once
        (intended: no default). Renumber and fix any step-number reference.
  - [ ] Rewrite the second "limit of the pattern" bullet ("systemd serializes
        a unit only against itself …"): stagger is kept, for provider quota
        (minute and daily share EODHD's allowance, 30 minutes apart) and
        because Kalshi × tick and Kalshi × heavy-minute overlaps measured
        harmless. Name the two reopen triggers from Technical Decision 3.
  - [ ] Success: "carries no resource limits yet" and "does not exist yet" are
        gone; steps are numbered consecutively
  - [ ] Effort: 1

- [ ] **3.2 Plan, Revision Log, CHANGELOG**
  - [ ] `user/architecture/900-slices.foundation-cleanup.md`: mark Future Work
        item 4 `[x]` and append, leaving existing text intact, one sentence
        each: closed by 233 with `MemoryMax` only (weights inert on this
        host); stagger kept on existing evidence; the two reopen triggers.
  - [ ] `user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md`:
        append, as the last entry of the Revision Log, a dated entry in the
        existing bullet style: **"Cross-source arbitration"**: `MemoryMax`
        only, weights not set (233 design, Technical Decision 1), mechanism is
        calendar stagger kept on existing evidence. Do not rewrite the
        Technical Considerations paragraph.
  - [ ] `CHANGELOG.md`, `## [Unreleased]` → `### Added`: one user-facing
        entry. A runaway acquisition pass is now killed inside its own slice
        rather than the host's OOM killer choosing among PostgreSQL backends;
        the bound takes effect at the release that follows 234.
  - [ ] Commit: `docs: record 233 arbitration decision in runbook, plan, log`
  - [ ] Success: item 4 checked with original wording intact; the log entry
        is last; the CHANGELOG makes no claim that the bound is in force
  - [ ] Effort: 1

---

## Section 4 — Validation and close-out

- [ ] **4.1 Checks and unit tier**
  - [ ] `ruff check` on touched `.py` files, scoped `ruff format --check`,
        mypy per the conventions.
  - [ ] `git diff main --stat` shows only: the script, its test package,
        `test_units.py`, the slice file, the three documents, the runbook,
        the CHANGELOG and the findings note. Nothing under `src/`.
  - [ ] `uv run python scripts/run_tests.py unit`. Fix any failure not in the
        known pre-existing list; re-run a suspected flake in isolation first.
  - [ ] Success: clean; green apart from known pre-existing failures, each
        listed in the findings note
  - [ ] Effort: 1

- [ ] **4.2 Walkthrough, findings and close-out**
  - [ ] Re-run walkthrough steps 1–3 and record the outputs. The file's
        `MemoryMax` must equal a fresh script run (the window moves daily); if
        it differs, re-derive and update the file and comment first.
  - [ ] Read-only: confirm the slice plan's 234 entry still carries the issue
        #33 obligation. Do not edit it.
  - [ ] Findings note, one sentence each: the Saturday 6.2 GiB minute-pass
        peak (every firing day only; others 75–144 M; probable cause the
        trailing phase's gap-row seeding; not investigated); Kalshi contract
        diff "none"; realtime paths "neither ruled out, a streaming source is
        the reopen trigger"; data-correctness contract "no rows, no data path
        touched". State that the bound is declared, not in force, until 234's
        cutover.
  - [ ] Update the design with any walkthrough corrections and the note's
        path; set the design and this file to `status: complete` and bump
        `dateUpdated`.
  - [ ] Commit: `docs: record 233 implementation findings`
  - [ ] Success: committed on the slice branch; no merge step performed
  - [ ] Effort: 1
