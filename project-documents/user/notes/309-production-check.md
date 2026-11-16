---
docType: notes
layer: project
project: trading-data
slice: time-bars-from-held-bars-and-ticks
audience: [human, ai]
description: Slice 309 production check after release 0.27.3 - bars served from ticks for ES and GC, health clean, consumers told
dateCreated: 20261116
dateUpdated: 20261116
status: complete
---

# 309 production check (release 0.27.3, 2026-11-16)

Production runs 0.27.3 (`mt --version`), `mt-serve.service` active, `/api/v1/health` `{"status":"ok","db":"ok","coverage":"ok","tick_db":"ok"}`. No migration was added; the deploy was `scripts/upgrade_production.py v0.27.3`, PASS.

## Criterion 1: bars are served

`GET /api/v1/futures/products/{ES,GC}/bars?start=2024-12-02&end=2024-12-03&granularity=<size>` (`end` is exclusive; `end=2024-12-02` is a 422 "range is empty"). Every response had `sources` naming `ticks` for 2024-12-02 and `applied` listing `end, format, from, granularity, rule, start, until`.

| Product | 1h | 1m | 15s | 1d |
|---|---|---|---|---|
| ES | 23 | 1380 | 5357 | 1 |
| GC | 23 | 1379 | 5362 | 1 |

## Criterion 7: health

`sudo mt-run data health` exited 0, verdict `healthy`, no FAIL line. No provider bar or statistics rows exist (none were bought).

## Criterion 8: consumers told

2026-11-16, by `SendMessage`: mt-ui and trading-engine each received that bars are served, the new shape (`granularity` any size, `from`/`until`, `sources`, `applied`) and a pointer to `docs/api/reference.md` section 6.8.
