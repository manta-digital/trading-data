---
docType: reference
project: trading-data
dateCreated: 20260927
dateUpdated: 20260930
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

## Provider batch manifest

`batch/GLBX-20240930-USM7UXXJBA/manifest.json` is the `manifest.json` of a
trades job the provider delivered (adopted in slice 223), byte for byte except
that the account segment of every download URL reads `REDACTED`. It keeps
adoption's manifest parser honest against the real format.

## Real day slices (slice 225)

`real/` holds small cuts of purchased days from `/data/tick-archive`, made by
`scripts/cut_tick_fixtures.py`. Each tier file keeps the provider's header
bytes unchanged (DBN v1, `parent`, spanning exactly its UTC day) and about
3,700 of the day's records: the first after 00:00 UTC, both sides of the
daily break (so two sessions), the last before 24:00 UTC, and up to 100
records of every other instrument (calendar spreads, back months).

| File | Source job | Records | Notes |
|---|---|---|---|
| `glbx-mdp3-20240903.trades.dbn.zst` | `GLBX-20240930-USM7UXXJBA` | 3,774 | 6 instruments incl. 3 spreads; 73 repeated `(instrument_id, ts_event, sequence)` triples |
| `glbx-mdp3-20241203.tbbo.dbn.zst` | `GLBX-20250123-XT4GD5UM6C` | 3,718 | 5 instruments incl. 2 spreads; 53 repeated triples |
| `glbx-mdp3-20241203.trades.dbn.zst` | derived | 3,718 | **Not purchased.** The adopted jobs share no day, so the supersession fixture (225 FR6) is the `tbbo` slice's records cut to their leading trade fields, with the header's schema set to `trades` |
| `glbx-mdp3-20240903.definition.dbn.zst` | `GLBX-20260930-DLDYL5DM8Q` | 61 | whole file |
| `glbx-mdp3-20241203.definition.dbn.zst` | `GLBX-20260930-HVGRLYKHRN` | 61 | whole file |
