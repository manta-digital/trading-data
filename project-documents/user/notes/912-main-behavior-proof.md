---
item: 912
project: trading-data
type: note
status: complete
created: 2026-08-03
updated: 2026-08-03
---

# Slice 912 — Criterion 1 verified to fail against pre-912 behavior

Task 5.1 requires the resume test to fail against the behavior it replaces.
This records that verification.

## Method

The comparison tree is commit `643e639` — the merge base of
`912-slice.daemon-cycle-correctness` and `trading-data-maintenance`, and the
parent of the slice's first implementation commit. The branch point is used
rather than the current `main` deliberately: `main` is under active development,
so a proof against its moving tip would not be reproducible, whereas the fork
point is the exact code this slice replaces and never changes.

```
git worktree add --detach <scratch>/pre912 643e639
PYTHONPATH=<scratch>/pre912/src uv run python prove_main_fails.py
```

The script drives the **real pre-912 `Runner`** through the same scenario as
`test_interrupted_pass_resumes_at_unreached_symbols`: six symbols, a daily cycle
that crashes after reaching two, one simulated hour, `--forever`. It printed
`manta_trading loaded from: <scratch>/pre912/src/manta_trading/__init__.py`,
confirming the old tree was the one under test and not the working tree.

## Result

```
A. daily_cycle_due 30m after an interrupted pass: False
   next day:                                      True
B. cycles run in one simulated hour: 1
   symbols each cycle was handed:    [['AAA','BBB','CCC','DDD','EEE','FFF']]
   symbols actually attempted:       ['AAA', 'BBB']
C. restart was handed:                [['AAA','BBB','CCC','DDD','EEE','FFF']]

FAILS against pre-912, as required:
  - 5.1 assertion 'the pass was never retried':
      len(pending_seen) >= 2 -> got 1
  - 5.1 restart assertion:
      expected ['CCC','DDD','EEE','FFF']
      -> got ['AAA','BBB','CCC','DDD','EEE','FFF']
```

Both defects reproduce, and reproduce independently:

- **A/B — the pass is never retried.** `daily_cycle_due` compares
  `last_daily_cycle_start_utc.date()` against today, so the stamp taken *before*
  the crashed cycle closes the gate for the remainder of the UTC day. CCC
  through FFF are not fetched at all that day. This is GitHub issue #7 as
  observed on prod on 2026-08-03.
- **C — a restart re-runs everything.** With no durable derivation, the fresh
  process is handed the full six-symbol scope. This is the defect that *masked*
  the first one: an operator restart looked like recovery, because re-running
  the whole pass happens to reach the unfetched symbols.

The same scenarios pass on the slice branch — see
`test/unit/data/acquisition/daemon/test_daily_resume_behavior.py`, where the
restart case asserts the second process is handed exactly `['CCC','DDD','EEE','FFF']`.

## Disposal

The scratch worktree was removed after the run (`git worktree remove`). Nothing
in the repository depends on it; re-running the proof needs only the commit id
above.
