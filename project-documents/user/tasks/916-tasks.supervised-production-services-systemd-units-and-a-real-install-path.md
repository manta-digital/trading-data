---
docType: tasks
slice: supervised-production-services-systemd-units-and-a-real-install-path
project: trading-data
lldReference: user/slices/916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [128, 908]
interfaces: [907, 913, 915]
projectState: >
  Production on manta9000 (192.168.1.144) is the developer checkout at
  ~/source/repos/manta/trading-data, run by hand. No systemd units exist, no
  /opt/manta-trading, no /etc/manta-trading.env. The 2026-08-19 crash brought
  back PostgreSQL and the backup cron but not acquisition. deploy/systemd/ holds
  two never-installed .service.tmpl files from slice 128 plus a journald drop-in.
  The host has no passwordless sudo.
status: complete
dateCreated: 20260822
dateUpdated: 20260823
---

# Tasks: Supervised production services — systemd units and a real install path

## Context summary

Production has no supervision: a reboot or crash stops acquisition silently and
nothing brings it back. This slice installs a dedicated, pinned checkout at
`/opt/manta-trading` under a `manta-trading` service account, and puts three
things under systemd — the daily pass, the minute pass, and `mt serve`.

The production invocation does not change: `mt data daemon run {--daily|--minute}
--stop-when-done` is still one bounded pass that exits. A timer fires it instead
of a human.

Read the design before starting. The decisions that shape these tasks are:
oneshot-passes-on-timers (not a looping daemon), the dedicated `/opt` install,
one PM-run install script that **enables nothing**, and a cutover that is a
single reversible `systemctl enable --now`.

## Execution notes

- **Root boundary.** manta9000 has no passwordless sudo. Every task marked
  **[PM]** is executed by the Project Manager. **[agent]** tasks need no
  elevation. Do not attempt to work around a **[PM]** task.
- **Stage everything in the repo.** Multi-line pastes into the host's zsh have
  already proven unreliable. Nothing longer than a single command line is ever
  relayed for the PM to paste — it goes in a file that is committed first.
- **Nothing is enabled until Group F.** Groups A–E leave production running
  exactly as it does today, by hand from the dev checkout. If a step would start
  or enable a unit before F, that is a stop-and-ask.
- **The dev checkout is never modified.** It stays runnable throughout, and is
  the rollback path.
- **Nothing here takes more than minutes.** The single bounded wait is E.1: a
  `--stop-when-done` pass that meets a closed cadence gate *sleeps* rather than
  exiting, for at most 30 minutes (the gate is UTC-midnight + 30 min, and the
  retry interval is 30 min). There is no "already ran today, come back tomorrow"
  path — the due-predicate deliberately makes no UTC-day comparison. Check
  `acquisition_state.last_daily_cycle_end_utc` before E.1 and the wait is
  usually zero. A wait, if it happens, announces itself every 5 minutes.
- **No task waits on a wall-clock event.** Timer *firing* is proven by
  `systemctl start` on the same unit the timer activates, plus `list-timers` for
  the schedule. The one reboot (G.4) is a PM-scheduled moment executed
  immediately, not a wait.
- Credentials never enter a tracked file. `deploy/manta-trading.env.example`
  carries placeholders only.

---

## Group A — Unit files in the repo

Effort: 2/5. All **[agent]**, all repo-only. Nothing is installed here.

New files live in `deploy/systemd/`. Every unit gets
`Documentation=` pointing at the current repo URL — the existing templates carry
a stale `manta-trading/trading` URL from before the rename.

- [x] **A.1 [agent] Create `manta-acquisition.slice`**
  - [x] `[Unit]` with a `Description` only. **No resource settings** —
        `IOWeight`/`CPUWeight`/`MemoryMax` are deliberately absent (design:
        Deferred). This unit exists so units have a home, not to tune anything
  - [x] Success: file exists; `systemd-analyze verify` reports no errors

- [x] **A.2 [agent] Create `mt-daily-pass.service`**
  - [x] `Type=oneshot`, `User=manta-trading`, `Slice=manta-acquisition.slice`,
        `WorkingDirectory=/opt/manta-trading`,
        `EnvironmentFile=/etc/manta-trading.env`
  - [x] `ExecStart=/opt/manta-trading/.venv/bin/mt data daemon run --daily
        --stop-when-done` — the venv entry point directly. **Not `uv run`**: uv
        is deploy-time only and would risk a resolve/sync at service start
  - [x] `TimeoutStartSec=infinity` with the comment explaining why (a pass
        legitimately runs long; the 90s default would kill it mid-fetch)
  - [x] `TimeoutStopSec=300` with the comment explaining why (the runner traps
        SIGTERM and exits between symbols, but its sleeps are capped at 60s, so
        the 90s default would SIGKILL a shutdown that is working correctly)
  - [x] Hardening: `NoNewPrivileges=true`, `ProtectSystem=full`,
        `ProtectHome=true`, `PrivateTmp=true`; journal for stdout and stderr
  - [x] **No `Restart=`** — recovery is the next timer firing. **No `[Install]`
        section** — the timer is what gets enabled, not the service
  - [x] Success: `systemd-analyze verify` clean; no `[Install]` section present

- [x] **A.3 [agent] Create `mt-daily-pass.timer`**
  - [x] `OnCalendar=*-*-* 00:35:00 UTC` and `OnCalendar=*-*-* 12:35:00 UTC` —
        after the 00:30 UTC daily gate; the second firing is a same-day catch-up
  - [x] `Persistent=true` (post-reboot catch-up), `WantedBy=timers.target`
  - [x] Success: `systemd-analyze calendar '*-*-* 00:35:00 UTC'` resolves to the
        expected next elapse; `systemd-analyze verify` clean

- [x] **A.4 [agent] Create `mt-minute-pass.service`**
  - [x] Identical to A.2 except `ExecStart` uses `--minute`
  - [x] Success: as A.2. Confirm the two services differ **only** in the flag
        and the description

- [x] **A.5 [agent] Create `mt-minute-pass.timer`**
  - [x] `OnCalendar` at 01:05 and 13:05 UTC — staggered 30 minutes behind daily
        so the two passes never start simultaneously
  - [x] `Persistent=true`, `WantedBy=timers.target`
  - [x] Success: no `OnCalendar` value collides with A.3's

- [x] **A.6 [agent] Create `mt-serve.service`**
  - [x] `Type=simple`, same `User`/`EnvironmentFile`/hardening block,
        `ExecStart=/opt/manta-trading/.venv/bin/mt serve`
  - [x] `Restart=on-failure`, `RestartSec=10s`, `StartLimitBurst=5`,
        `StartLimitIntervalSec=300s`, `WantedBy=multi-user.target`
  - [x] Success: `systemd-analyze verify` clean; `[Install]` section present
        (this unit *is* enabled directly, unlike the pass services)

- [x] **A.7 [agent] Create `deploy/manta-trading.env.example`**
  - [x] `MT_TIMESCALE_DB_URL` and `MT_EODHD_API_KEY` with **placeholder values
        that are obviously placeholders**, plus commented optional tuning
        (`MT_LOG_LEVEL`, `MT_DAILY_CYCLE_RETRY_MINUTES`)
  - [x] `MT_TIMESCALE_MAINTENANCE_URL` is **absent**, with a comment saying why:
        the DDL credential stays out of service environments (slice 913)
  - [x] Success: no real credential in the file; `git diff` shows placeholders

- [x] **A.8 [agent] Delete the superseded slice-128 templates**
  - [x] Remove `deploy/systemd/mt-daily-daemon.service.tmpl` and
        `mt-minute-daemon.service.tmpl` — they assume the looping daemon form and
        a templated user, both superseded
  - [x] Leave `journald-manta-trading.conf` **unchanged**; only its install
        becomes real
  - [x] Success: `ls deploy/systemd/` shows six units, the slice, and the
        journald drop-in, and no `.tmpl` files

- [x] **A.9 [agent] Verify the unit set as a whole**
  - [x] `systemd-analyze verify deploy/systemd/*.service deploy/systemd/*.timer
        deploy/systemd/*.slice` — clean, allowing only warnings that stem from
        the service account and `/opt` not existing yet on the dev machine
  - [x] Confirm by grep: every acquisition unit sets
        `Slice=manta-acquisition.slice`; no unit carries the pre-rename
        `Documentation=` URL; no `OnCalendar` value appears in two timers
  - [x] Success: the checks above pass, and the findings are recorded in the
        commit message

- [x] **A.10 [agent] Commit the unit files**
  - [x] Success: units, slice, env example, and the template deletions are one
        reviewable commit

---

## Group B — The install script

Effort: 3/5. All **[agent]** (writing it); it is *executed* by the PM in Group D.

`deploy/install-production.sh` is the slice's only substantial new I/O path.
Every step is check-then-act so that the recovery for any failure is "fix the
cause, re-run the whole script." Keep it plain shell — no templating engine.

- [x] **B.1 [agent] Script skeleton, argument handling, and step logging**
  - [x] `set -euo pipefail`. Require `--ref <tag-or-sha>`; fail with usage if
        absent. **No default ref** — a silent default would let `/opt` track
        something nobody chose
  - [x] Refuse to run as non-root with a clear message
  - [x] Each step announces itself before acting, so a failure names the step it
        died in
  - [x] Success: `--help` prints usage; running without `--ref` exits non-zero
        with a message naming the missing argument

- [x] **B.2 [agent] Step 1 — service account**
  - [x] `getent passwd manta-trading`; create only if absent (system account,
        `nologin` shell, home `/opt/manta-trading`)
  - [x] If present: verify shell and home match and continue. If present with a
        **different shape, abort** — adopting a colliding or hand-made account
        silently is a PM decision, not a script's
  - [x] Success: creating, matching-and-skipping, and mismatched-abort are three
        distinct outcomes with distinct messages

- [x] **B.3 [agent] Step 2 — checkout**
  - [x] If `/opt/manta-trading/.git` is absent: remove any leftover directory
        (a died-mid-way clone leaves no usable `.git`), then clone at `--ref`
  - [x] If present: `git status --porcelain` must be empty and `origin` must
        match, else **abort untouched**
  - [x] A clean checkout at a *different* ref is fetched and checked out to the
        requested ref — the guard is about local modifications only, and must
        never block a retry
  - [x] Ownership is `manta-trading:manta-trading` throughout
  - [x] Success: the four paths (fresh clone, clean re-run, different-ref
        checkout, dirty-tree abort) are each reachable and each print why

- [x] **B.4 [agent] Step 3 — virtualenv**
  - [x] `uv sync --frozen` as the service account. Do **not** delete `.venv` on
        failure — `uv sync` is resumable and reconciles a partial venv next run
  - [x] Then verify `/opt/manta-trading/.venv/bin/mt --version` runs; stop hard
        if it does not, since every `ExecStart` depends on that exact path
  - [x] Success: the version check gates the rest of the script

- [x] **B.5 [agent] Step 4 — environment file**
  - [x] Install `/etc/manta-trading.env` from the example **only if it does not
        exist**. Never overwrite, never merge — the PM's filled-in credentials
        must survive every re-run
  - [x] Re-assert ownership `root:manta-trading` and mode `0640` on every run
  - [x] Success: a second run leaves an existing file byte-identical (prove by
        checksum) while still correcting mode or owner if they drifted

- [x] **B.6 [agent] Step 5 — unit files and journald drop-in**
  - [x] Copy all six units plus `manta-acquisition.slice` to
        `/etc/systemd/system/`, and `journald-manta-trading.conf` to
        `/etc/systemd/journald.conf.d/manta-trading.conf`
  - [x] Copy unconditionally — the repo is the source of truth and these files
        hold no host-local state
  - [x] Success: a partial failure here leaves units present but unreferenced
        (inert, because nothing is enabled) and the re-run completes the set

- [x] **B.7 [agent] Step 6 — reload, and the closing message**
  - [x] `systemctl daemon-reload` unconditionally at the **end** of every run,
        including runs where step 5 changed nothing. Reload last, so a
        half-copied unit set is never loaded
  - [x] Restart `systemd-journald` so the journald caps take effect
  - [x] Print the next two steps (fill the env file; run one pass by hand) and
        restate plainly that **nothing has been enabled**
  - [x] Success: `systemctl list-unit-files 'mt-*'` after a run shows every unit
        as `disabled`

- [x] **B.8 [agent] Static-check the script**
  - [x] `bash -n` and `shellcheck` clean (fix or justify each suppression
        inline — no blanket disables)
  - [x] Read it against the design's per-step failure table and confirm each
        enumerated failure state matches what the code actually does
  - [x] Success: both checks pass and the read-through found no divergence, or
        the divergence was fixed

- [x] **B.9 [agent] Commit the install script**

---

## Group C — Code and documentation

Effort: 2/5. All **[agent]**.

- [x] **C.1 [agent] Fix the `serve.py` help text**
  - [x] `src/manta_trading/cli/commands/serve.py` — the `--workers` help says
        "slice 155 adds supervised launch". Slice 155 is dead. Replace with a
        reference to the `mt-serve` systemd unit
  - [x] Success: no slice number appears in user-facing help text

- [x] **C.2 [agent] Test the help text**
  - [x] Assert `mt serve --help` output contains no `slice` reference and names
        the unit. Follow the existing CLI test conventions in
        `test/` rather than inventing a new pattern
  - [x] Success: the test fails against the old string and passes against the new

- [x] **C.3 [agent] Rewrite `production-deploy.md` around the `/opt` install**
  - [x] Delete the "Future target — /opt + systemd" section; the target exists
  - [x] Close all three "Not yet documented" items by stating the answers: where
        the env file lives, what re-invokes the passes (the timers, with their
        schedule and the gate interaction), and the restart-after-reboot
        procedure — which is "nothing", plus how to verify that
  - [x] Add the update procedure: fetch, checkout a ref, `uv sync` as the
        service account, `systemctl restart mt-serve`; passes pick up new code at
        their next firing. Migrations stay a separate operator step with the
        maintenance credential
  - [x] Add status-checking via `systemctl` alongside the existing
        `acquisition_state` queries
  - [x] **Keep** the timing-constraints section (gate wait, retry interval, cheap
        re-runs) — it now explains timer behavior
  - [x] State plainly which install is production, and how to tell a supervised
        run from a manual one (`_SYSTEMD_UNIT` in the journal)
  - [x] Success: no "Future target" and no "Not yet documented" remain in the file

- [x] **C.4 [agent] Add the "adding a source" procedure to the runbook**
  - [x] The naming pattern (`mt-{source-or-cadence}-pass` for a bounded pass,
        `mt-{name}` for a long-running service) and the checklist: copy a unit
        pair, change `ExecStart`, pick a non-colliding `OnCalendar`, set
        `Slice=manta-acquisition.slice`, install, enable
  - [x] State the two limits: `oneshot`+timer suits a **bounded** pass — a
        streaming subscription is a `Type=simple` unit like `mt-serve`, with
        backfill as a separate pass — and systemd serializes a unit only against
        *itself*, so cross-source arbitration does not exist (Future Work 4)
  - [x] Success: a reader could add a Kalshi pass without reopening the design

- [x] **C.5 [agent] Add the "pausing a source" procedure to the runbook**
  - [x] Three levels: `stop` (this boot only), `disable --now` (**the pause that
        survives reboot**), `mask` (nothing can start it, even manually)
  - [x] `systemctl stop` on a *running* pass is a clean SIGTERM exit, not a kill
  - [x] **Resuming fires a catch-up immediately** because of `Persistent=true`;
        name `/var/lib/systemd/timers/stamp-mt-{unit}.timer` as the escape hatch
        when a resume should not backfill
  - [x] Success: pause, resume, and their side effects are all documented

- [x] **C.6 [agent] Update the two documents that point at slice 916 by name**
  - [x] `backup-and-restore.md`: the "916 will decide cron vs timers" pointer
        becomes "decided 2026-08-22: backups stay on cron"
  - [x] The crontab comment wording is updated **via the runbook** — the crontab
        itself is PM-owned host config and is not edited by an agent
  - [x] Success: no document still describes the cron-vs-timer question as open

- [x] **C.7 [agent] Commit the code and documentation changes**

---

## Group D — Install on the host, inert

Effort: 2/5. Every root step is **[PM]**. Nothing is enabled in this group.

- [x] **D.1 [agent] Choose and record the ref to pin**
  - [x] A tag or commit SHA that contains Groups A–C. Record the exact value;
        every later step uses it
  - Recorded ref: `942b5422dd9d5eb8c77439bdc72f6fee06a5e772` (pushed to origin; contains Groups A–C plus the UV_PYTHON_INSTALL_DIR fix found in the first host install attempt — the earlier bf0eb0c pin predated that fix).
  - [x] Success: the ref is recorded here, and `git log` confirms it contains
        the unit files and the install script

- [x] **D.2 [PM] Run the install script**
  - [x] `sudo deploy/install-production.sh --ref <recorded ref>` from the dev
        checkout
  - [x] Success: exits 0 and prints the "nothing has been enabled" message

- [x] **D.3 [PM] Run it a second time — idempotence**
  - [x] Same command, unchanged
  - [x] Success: exits 0; no account recreated; the env file is byte-identical
        (checksum before and after); nothing became enabled

- [x] **D.4 [PM] Fill `/etc/manta-trading.env`**
  - [x] `sudoedit /etc/manta-trading.env`; set `MT_TIMESCALE_DB_URL` (the
        DML-only application credential) and `MT_EODHD_API_KEY`. Values come
        from the dev checkout's `.env`; **the file itself is never copied**
  - [x] Success: the file holds real values at mode 0640 `root:manta-trading`

- [x] **D.5 [agent] Verify the install without starting anything**
  - [x] `/opt/manta-trading` exists, owned by `manta-trading`, at the pinned ref,
        clean working tree; `.venv/bin/mt --version` runs
  - [x] **No `.env` file inside `/opt/manta-trading`** — configuration reaches
        services only through `/etc/manta-trading.env`
  - [x] `MT_TIMESCALE_MAINTENANCE_URL` is absent from the env file
  - [x] No credential appears in any tracked file (grep the repo)
  - [x] `systemctl list-unit-files 'mt-*'` shows all units **disabled**;
        `systemctl list-timers 'mt-*'` is empty
  - [x] Success: every check above holds. Production is still running by hand
        from the dev checkout, unchanged

---

## Group E — Prove one pass under systemd, before cutover

Effort: 1/5.

- [x] **E.1 [PM] Run one daily pass through its unit**
  - [x] First check `acquisition_state.last_daily_cycle_end_utc`. If it is more
        than 30 minutes ago and the clock is past 00:30 UTC, the pass starts
        working immediately; otherwise it sleeps out the gate, at most 30
        minutes, restating the remaining time every 5 minutes
  - [x] `sudo systemctl start mt-daily-pass.service`, then
        `journalctl -u mt-daily-pass.service -f`
  - [x] Success: normal pass output; the unit ends `inactive (dead)` with
        `status=0/SUCCESS`

- [x] **E.2 [agent] Confirm the supervised pass matches a manual one**
  - [x] Same log shape as today's by-hand invocation, and the same
        `acquisition_state` effect, now running as `manta-trading` from `/opt`
  - [x] **If E.1 exited "no actionable work"**, that is a valid pass but a weak
        comparison — it never touched the fetch path. Do not wait for one; run
        E.3 instead and compare there. The minute gate is one minute rather than
        thirty, so a minute pass almost always has real work
  - [x] Confirm the journal identifies it by `_SYSTEMD_UNIT`, which is what
        distinguishes supervised runs from manual ones
  - [x] Success: no behavioral difference beyond user and working directory.
        **A difference here stops the cutover** — investigate before Group F

- [x] **E.3 [PM] Run one minute pass through its unit**
  - [x] `sudo systemctl start mt-minute-pass.service`
  - [x] Success: as E.1

- [x] **E.4 [agent] Confirm the minute pass as in E.2**

---

## Group F — Cutover

Effort: 1/5, risk concentrated here. This is the one explicit step.

- [x] **F.1 [PM] Enable the timers and the API server**
  - [x] `sudo systemctl enable --now mt-daily-pass.timer mt-minute-pass.timer
        mt-serve.service`
  - [x] From this instant production is the `/opt` install. Operator practice
        changes: passes and `mt serve` are no longer started by hand
  - [x] Success: the command exits 0

- [x] **F.2 [agent] Verify the timers and the API**
  - [x] `systemctl list-timers 'mt-*'` shows both timers with sane next-fire
        times consistent with the design's schedule
  - [x] `systemctl is-active mt-serve.service` is `active`; the API answers on
        its configured port
  - [x] Success: both checks pass

- [x] **F.3 [agent] Verify journald caps and the acquisition slice**
  - [x] The 2 GiB / 200 MiB caps are in effect (`journalctl --header` or a
        disk-usage check)
  - [x] `systemctl status manta-acquisition.slice` shows the pass units as its
        members when one runs
  - [x] Success: caps confirmed; units are in the slice

---

## Group G — Prove supervision, pause, and rollback

Effort: 2/5. All **[PM]** for the acting steps; verification is **[agent]**.

- [x] **G.1 [PM] Crash supervision**
  - [x] `sudo kill -9 "$(systemctl show -p MainPID --value mt-serve.service)"`
  - [x] Success: `systemctl status mt-serve.service` shows it active again with
        the restart count incremented

- [x] **G.2 [PM] Clean stop of a running pass**
  - [x] Start a pass by hand (`systemctl start mt-daily-pass.service`), then
        `systemctl stop mt-daily-pass.service` while it is running
  - [x] Success: the journal shows the runner's clean-exit path, **not**
        `Killed` or `signal=KILL`; `acquisition_state` shows a resumable
        position. A SIGKILL here means `TimeoutStopSec` is too low — fix A.2/A.4

- [x] **G.3 [PM] Pause one source**
  - [x] `sudo systemctl disable --now mt-minute-pass.timer`
  - [x] Success: `systemctl list-timers 'mt-*'` no longer lists it; daily is
        still listed and `mt-serve` still active

- [x] **G.4 [PM] Reboot survival, with the pause still in force**
  - [x] PM-scheduled moment, executed immediately — this is not a wait
  - [x] Prefer a moment when no pass is mid-flight, but do not wait for one:
        an interrupted pass resumes where it stopped (slice 912), which is
        exactly the property the reboot is testing
  - [x] `sudo reboot`
  - [x] Success after boot, with **no operator action**: `mt-serve` is active,
        the daily timer is back in `list-timers`, and the paused minute timer is
        **still absent**. `Persistent=true` fires any daily schedule missed while
        the host was down

- [x] **G.5 [PM] Resume the paused source**
  - [x] `sudo systemctl enable --now mt-minute-pass.timer`
  - [x] Success: the timer returns to `list-timers`, and the `Persistent=true`
        catch-up pass fires immediately — the documented behavior from C.5

- [x] **G.6 [PM] Rollback rehearsal**
  - [x] `sudo systemctl disable --now mt-daily-pass.timer mt-minute-pass.timer
        mt-serve.service`
  - [x] Run one pass manually from the dev checkout, exactly as before this slice
  - [x] Re-enable the three
  - [x] Success: rollback works, the dev checkout was never modified, and
        re-enabling restores the supervised state

- [x] **G.7 [agent] Record the measured results of G.1–G.6**
  - [x] Restart counts, timer states before and after the reboot, the clean-stop
        journal line, and the rollback outcome
  - [x] Success: each success criterion in the design is answered with an
        observation, not an assertion

---

## Group H — Close the slice

Effort: 1/5. All **[agent]**.

- [x] **H.1 [agent] Fold the measured results into the runbook**
  - [x] `production-deploy.md` gains anything Groups D–G proved that the draft
        got wrong or left vague. A correction found while running belongs in both
        the runbook and this task file
  - [x] Success: the runbook describes what actually happened on the host

- [x] **H.2 [agent] Refine the design's verification walkthrough**
  - [x] Replace the draft walkthrough with the commands as actually run,
        including anything that needed a correction
  - [x] Success: the walkthrough is reproducible by someone who was not present

- [x] **H.3 [agent] Update slice status and the plan entry**
  - [x] Design frontmatter `status: complete`; slice-plan entry 17 checked off
        with its outcome recorded
  - [x] Success: no document still describes this slice as open

- [x] **H.4 [agent] Final commit**
