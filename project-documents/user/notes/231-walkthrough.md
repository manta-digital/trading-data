---
docType: notes
slice: api-surface-coverage-futures-tick
project: trading-data
dateCreated: 20261013
dateUpdated: 20261013
---

# Slice 231 walkthrough on `trading_tick`

Run on 2026-10-13 on manta9000, from branch
`231-slice.api-surface-coverage-futures-tick`, with the repo's `.env` loaded:
the dev tick database (`MT_TICK_DB_URL`, manta9000:5433) and the production
primary for the calendar and the equity routes. Every request is a `GET`; the
tick pool's sessions are read-only, and the one direct database read below runs
under `default_transaction_read_only=on`.

## Load-tier gate

The load tier is gated by a **manual run**, not by CI.
`.github/workflows/ci.yml` publishes on tags and runs no test tier, and the
load tier needs the private test cluster, so CI cannot run it. The gate for
this slice is the run recorded under "Load tier" below. Wiring CI is not part
of this slice; if the Project Manager wants it, it is a separate slice (907
already names the load tier in its scope).

## Servers

```bash
set -a; source .env; set +a
uv run mt serve --host 127.0.0.1 --port 8137                                   # steps 1–6, 9
MT_TICK_DB_URL='postgresql://x@127.0.0.1:1/x' uv run mt serve --host 127.0.0.1 --port 8138   # step 7
MT_TICK_DB_URL= uv run mt serve --host 127.0.0.1 --port 8139                   # step 8
```

Three servers at once rather than restarts; each is one worker.

**Correction to the design's step 8.** `env -u MT_TICK_DB_URL uv run mt serve`
does not unset the tick database: `Settings` reads `.env` itself, so an absent
environment variable falls back to the file's value. An *empty* variable,
`MT_TICK_DB_URL=`, overrides the file and reads as unset. The design's step 8
now says so.

## Steps

**1. Health.** Pass.

```console
$ curl -s localhost:8137/api/v1/health
{"status":"ok","db":"ok","coverage":"ok","tick_db":"ok"}
```

**2. Catalog.** Pass. Both active lines name ESH5 for 2024-12-31, and the
whole `active` array is equal (`jq -S`, `diff`) to `mt data tick active
--product ES --json`'s `lines`. 51 contracts; ESZ4 holds
`2024-08-30 → 2024-12-20`.

```console
$ curl -s localhost:8137/api/v1/futures/products | jq -c '[.products[0].active[] | {rule, raw_symbol, session_date}]'
[{"rule":"v.0","raw_symbol":"ESH5","session_date":"2024-12-31"},{"rule":"c.0","raw_symbol":"ESH5","session_date":"2024-12-31"}]
$ curl -s localhost:8137/api/v1/futures/products/ES/contracts | jq '.contracts | length'
51
```

**3. Product bars across a roll.** Pass. `segments` are ESU4 for 09-16 → 09-17,
then ESZ4 for 09-18 → 09-20; `unresolved` and `incomplete` empty; 5 bars. The
`bars` array equals `mt data tick get 1d --product ES --start 2024-09-16 --end
2024-09-21 --json`'s `rows` (`diff` of `jq -S`: no difference).

**4. Ticks within a session, and the ceiling.** Pass. The largest sessions in
the ledger (read-only query, current units) are ESU4 2024-09-06 (690,908),
ESH5 2024-12-20 (672,468) and ESH5 2024-12-18 (665,724). The design's
"687,287" was measured on 2026-10-12 against less held data; ESZ4 2024-12-18,
the step's session, is still far over the ceiling.

```console
$ curl -s 'localhost:8137/api/v1/futures/contracts/ESZ4/ticks?start=2024-12-18&end=2024-12-19'
{"error":"the request matches more than the 75,000 row limit; narrow start/end or from/until"}     # HTTP 422
$ curl -s '…/ticks?start=2024-12-18&end=2024-12-19&from=2024-12-18T14:30:00Z&until=2024-12-18T14:35:00Z' | jq -c '.count, .ticks[0]'
1792
{"ts_event":"2024-12-18T14:30:00.001440845Z","session_date":"2024-12-18","raw_symbol":"ESZ4","instrument_id":183748,"price":"6050.25","size":1,"side":"B","bid_px":"6050","ask_px":"6050.25","bid_sz":17,"ask_sz":18,"bid_ct":17,"ask_ct":13,"sequence":29838835}
```

**5. msgpack.** Pass. `Content-Type: application/x-msgpack`; the decoded body
equals the JSON body (1,792 ticks).

**6. Status and overview.** Pass. `/status`'s `tick` equals `mt data accounting
--json`'s `tick` (`diff`: no difference): ES, 64 sessions in scope, 61
complete, 27,691,412 records. The overview's `futures ticks` source is the same
instant as `mt data overview --json`'s: `2024-12-31T21:59:59.257498Z` (the API
spells UTC `Z`, the CLI `+00:00`), `unavailable` null.

**7. Tick database down.** Pass (port 8138).

```console
/health            {"status":"ok","db":"ok","coverage":"ok","tick_db":"unreachable","tick_detail":"couldn't get a connection after 5.00 sec"}
/futures/products  503 {"error":"unreachable: couldn't get a connection after 5.00 sec"}
/bars/AAPL?…       200
/status .tick      {"state":"unreachable","detail":"couldn't get a connection after 5.00 sec","products":[]}
/overview source   {"name":"futures ticks","newest":null,"unavailable":"unreachable: couldn't get a connection after 5.00 sec"}
```

The detail is psycopg_pool's checkout timeout message, not the socket error:
the pool's background connector holds that, and the request only sees that no
connection arrived within `TICK_API_CHECKOUT_TIMEOUT_SECONDS` (5 s).

**8. Not configured.** Pass (port 8139), with the step-8 correction above.

```console
/health            {"status":"ok","db":"ok","coverage":"ok","tick_db":"not_configured","tick_detail":"MT_TICK_DB_URL unset"}
/futures/products  503 {"error":"not configured (MT_TICK_DB_URL unset)"}
/bars/AAPL?…       200
/status .tick      {"state":"not_configured","detail":"MT_TICK_DB_URL unset","products":[]}
/overview source   {"name":"futures ticks","newest":null,"unavailable":"not configured (MT_TICK_DB_URL unset)"}
```

**9. Documentation gate.** Pass: `uv run python scripts/check_api_docs.py
--check` → `docs/api documentation is consistent with openapi.json`.
`reference.md` §6.1 states the exclusive-end rule and the
`segments`/`unresolved`/`incomplete` semantics.

**10. Production.** Not runnable in this slice: production has no
`MT_TICK_DB_URL` until 234, so its futures routes answer `503 not configured`,
which is the intended state.

## Found during implementation

- **The calendar leg of the roll path waited 30 s on an outage.** The roll
  functions' calendar read goes through `TradingCalendar`, whose own
  `ConnectionPool` used psycopg_pool's default 30 s checkout wait, so a
  refused calendar connection was a 30 s hang, not TD2's "within the connect
  timeout". `TradingCalendar` gained an optional `checkout_timeout`, and
  `open_calendar` (the tick path's only opener) passes
  `TICK_DB_CONNECT_TIMEOUT_SECONDS`. Measured after: a calendar outage on a
  product route answers `503` in about 10 s. `mt data tick active`/`get` gain
  the same bound.
- **`unit_of_measure_qty` is fixed-point**, like `min_price_increment` (ES
  stores 50,000,000,000 for 50). The contracts route serves both as exact
  decimal text (`"50"`, `"0.25"`).
- **There is no `partial` session status.** The design's TD5 example showed
  `{"status": "partial"}`; the statuses are `TickSessionStatus`'s, and
  `incomplete[].status` is typed as that enum in the schema.

## Load tier

`test/load/test_231_futures_api_nfr.py`, run twice on 2026-10-13 against the
test cluster (`MT_RUN_LOAD_TESTS=1 uv run python scripts/run_tests.py load --
test/load/test_231_futures_api_nfr.py -s`), about 17 minutes each, most of it
ingesting the archived 2024-12-18 `tbbo` day through the real pass. Medians of
five runs; the second run is the shipped code.

| Measure | Run 1 | Run 2 | Bound |
|---|---|---|---|
| 75,000 ESZ4 ticks through `until` | 1.704 s | 1.514 s | 3.0 s |
| Whole ESZ4 session, refused (`422`) | 0.264 s | 0.279 s | 0.55 s |
| 54-session `1m` product bars across the roll (74,280 bars) | over the ceiling* | 1.577 s | 3.2 s |
| `/status` added by the tick block | 0.032 s | −0.006 s | 0.25 s† |
| 75,000 rows, `model_construct` vs validated (build + dump) | 1.446 / 1.185 s | 1.364 / 1.165 s | validated < 1.2× construct |

\* Run 1 read a date range holding 55 sessions (Labor Day is a CME session), so
the read passed the ceiling; the test now takes exactly 54 seeded sessions.
† The difference sits inside `/status`'s own run-to-run spread (about 0.3 s),
so the bound is set above the noise rather than at 2× a near-zero median.
`/status` itself measured 2.56–2.59 s, the known 187 debt (bound 1.5 s).

**`model_construct` was slower.** Validated rows built and dumped faster in
both runs, so the models now validate, and TD5 records the measurement.
