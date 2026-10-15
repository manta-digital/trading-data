---
docType: notes
slice: tick-pass-production-wiring
project: trading-data
dateCreated: 20261014
dateUpdated: 20261014
---

# Slice 234: tick pass contract vs the Kalshi original

The standing 220 obligation (architecture, "The third source must not create a
third pass shape"): diff the tick copy against the Kalshi original and account
for every difference. 234 changes nothing in `data/tick/pass_contract.py`; it
adds the recorded production verb `mt data tick run` (`cli/tick_run.py`,
`cli/commands/tick_run_cmds.py`). Compared here against `run_pass` in
`cli/commands/kalshi.py` and `data/kalshi/collection_pass.py`. The 230 parity
test (`test/unit/data/tick/test_pass_contract_parity.py`) still passes unchanged.

| # | Aspect | Kalshi | Tick (234) | Why |
|---|---|---|---|---|
| 1 | Recorder | `pass_run_recorder(settings)`; every call through `asyncio.to_thread`; best-effort | Same, same, same | No difference |
| 2 | Phase progress | `CollectionPass(on_phase=…)` | `TickPass` has no `on_phase` (declared, 224 TD1); `run` wraps each acquisition phase in a reporter that writes its name first | Composition in the CLI; the contract is unchanged |
| 3 | Run outcome map | `PASS_RUN_OUTCOME_BY_SYNC_OUTCOME`, four members, exhaustiveness assert | `PASS_RUN_OUTCOME_BY_TICK_OUTCOME`, six members (adds `refused`, `in_flight` → `INCOMPLETE`), same assert | Tick's two extra outcomes are declared (224 TD1); neither is a provider fault or a failure |
| 4 | Exit codes | Kalshi's `EXIT_BY_OUTCOME` | `tick_exit.EXIT_BY_OUTCOME`, unchanged; no code added | Declared 224/225 vocabulary |
| 5 | Passes per run | One | Two (acquisition, then ingest always), folded to the worse by `TICK_OUTCOME_SEVERITY` | 234 TD2: one unit, one row, ingest must follow a refused purchase |
| 6 | Preflight failure | Row `FAILED`, `EXIT_PREFLIGHT`, detail `preflight failed`; nothing else runs | Row `FAILED`, `EXIT_PREFLIGHT`, detail names which pass refused and why; **the other pass still runs** | Follows from 5 (234 Data Flow step 4) |
| 7 | Unexpected exception | Row `FAILED`, exit code recorded as `EXIT_STORAGE` whatever the class, detail = class name; re-raised | Row `FAILED`, exit code = `exit_code_for(exc)` (None when the class has no mapped code), detail = class name; logged with `logger.exception`; re-raised | The row records the code the process actually exits with, never a stand-in value (CLAUDE.md: no silent fallbacks) |
| 8 | Detail text | Per-phase `name=outcome` list | `acquire=<o> ingest=<o>`, plus refusal text | One line per pass, not per phase (234 Data Flow step 4) |
| 9 | Progress counts | Phase names only | Phase names, plus `ingest` `done`/`total` rows at most every `TICK_HEARTBEAT_SECONDS` | The heartbeat is ingest's liveness signal (234 Data Flow step 2) |

**One tick-internal difference to know about.** `classify_pass`
(`pass_contract.py`) ranks phase outcomes *within* a pass with `partial` above
`refused` and `in_flight`. The 234 fold ranks the two *pass* outcomes with
`refused` and `in_flight` above `partial`, as the 234 design's Data Flow
step 4 specifies. The two orders answer different questions and do not meet:
`classify_pass` produces each pass's outcome, and the fold only combines
those. The fold's test derives its expectation from the exit vocabulary.

No unexplained difference.
