---
docType: followup-notes
sliceParent: 127
project: trading
dateCreated: 20260427
status: open
---

# Slice 127 follow-ups (post-merge)

Tracked here so they don't get lost between slice 127 closing out and
slice 128 starting. Address after the slice 127 commits land but before
slice 128 begins production deployment.

## 1. Pre-existing test failure in `test/unit/testmarketdb.py`

`TestMarketDBIntegration::test_write_and_read_daily_ohlcv` fails when
the unit suite is run with `.env` loaded. The test exercises
`MarketDB.writeDailyOHLCVAdjusted` against the real daily DB and hits:

```
ERROR  manta_trading.market.marketdb: Error writing daily OHLCV
adjusted data for AAPL: column "date" is of type date but expression
is of type smallint
LINE 6: VALUES ($1, $2, $3, $4, $5, $6, $7, ...
                                ^
HINT: You will need to rewrite or cast the expression.
```

**Diagnosis (preliminary):** the test constructs a DataFrame with
`date` as the first column (rather than the index) and the writer's
column-extraction path trips up. The error message points at parameter
positional types, suggesting the column reordering isn't matching the
INSERT column order.

**Provenance:** failing already at commit `c75b67f` (pre-slice-127
adjustment work). Hidden until now because the unit suite is normally
run without `.env`, so the `skipif(not MT_MARKET_DB_URL)` guard skips
the test.

**Scope:** small. Likely either a test bug (wrong DataFrame shape) or
a writer bug (column-order assumption that doesn't hold when the
DataFrame's `date` arrives as a column instead of index). Either way,
contained.

**Action:** open a small follow-up task; reproduce and fix.

## 2. Ruff baseline (153 legacy errors)

Project-wide `uv run ruff check src/ test/` reports 153 errors, all
pre-existing legacy:

* `Dict`/`List`/`Tuple` typing imports in older modules (UP006).
* Unused imports in early strategy files
  (`backtest/strategy/smacross*.py`).
* E402 module-level imports after code in
  `agents/newsagent.py` and `api/alphavantage/alphavantageapi.py`.
* `BLE001` blind-except findings in legacy code.
* A handful of `F841` and `F401` in older paths.

Slice 127 added zero net errors (briefly added 1 then cleared 2
during cleanup; net -1).

**Action:** spec'd a future cleanup slice or chore commit:
`uv run ruff check --fix --unsafe-fixes src/ test/` will autofix
~111 of these; the remainder need manual review (`E402`, `F821`,
`BLE001`).
