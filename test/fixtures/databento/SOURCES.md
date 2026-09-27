---
docType: reference
project: trading-data
dateCreated: 20260927
dateUpdated: 20260927
---

# Databento DBN sample files — provenance

Copied unmodified from the `databento/dbn` repository, `tests/data/`.

- Repository: https://github.com/databento/dbn
- Tag: `v0.70.0`
- Commit: `aa012adc6502e01b01d7b5c7836742dde21e93d8`
- Licence: Apache-2.0
- Fetched 2026-09-27 with
  `gh api -H "Accept: application/vnd.github.raw" repos/databento/dbn/contents/tests/data/<file>?ref=v0.70.0`

| File | Dataset | CME? | Schema | DBN version | Contents |
|---|---|---|---|---|---|
| `test_data.trades.v3.dbn.zst` | `GLBX.MDP3` | yes | `trades` | 3 | ESH1 (instrument id 5482), 2 records, 2020-12-28 |
| `test_data.tbbo.v3.dbn.zst` | `GLBX.MDP3` | yes | `tbbo` | 3 | ESH1 (5482), 2 records, 2020-12-28 |
| `test_data.mbp-1.v3.dbn.zst` | `GLBX.MDP3` | yes | `mbp-1` | 3 | ESH1 (5482), 2 records, 2020-12-28 |
| `test_data.trades.v2.dbn.zst` | `GLBX.MDP3` | yes | `trades` | 2 | Same records as the v3 trades file; proves the reader does not assume the current DBN version |
| `test_data.definition.v3.dbn.zst` | `XNAS.ITCH` | **no** | `definition` | 3 | MSFT, 2 records — **not a CME definition**; use for record-shape tests only |

These are correctness fixtures for the DBN file reader (slice 220, Technical
Decision 11). They are not throughput input; `scripts/bench_dbn_decode.py`
builds its own input from the trades file in memory.
