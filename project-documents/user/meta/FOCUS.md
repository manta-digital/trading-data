# FOCUS

Data must be bulletproof before anything else matters.

## Current Constraints

1. ~~**Test infrastructure incomplete**~~ ✅ pytest working, baseline established
2. **Daily data time gaps** - works but gap handling suspect, unverified
3. **Minute data not integrated** - service layer done, CLI still uses deprecated code
4. ~~**Broken import chain**~~ ✅ Fixed deprecated module internal imports (2026-01-19)
5. **No test data documentation** - don't know what's in test DB, can't create known-state tests

## What "Done" Looks Like

- ~~pytest installed, tests run~~ ✅
- All critical data functions have unit tests that pass
- Integration tests with known test data in documented state
- Daily data: gap handling verified or fixed
- Minute data: CLI uses new service, deprecated code removed
- Can answer: "is the data correct? how do we know?"

## Test Baseline (2026-01-19)

```
New-style tests (test_*.py): 152 passed, 0 failed
All unit tests: 345 passed, 28 failed (DB/API/env expected)
```

Note: Test count increased from 345→366 total after import fix allowed full collection.

## Next Actions

1. ~~Fix broken import: `ohlc.py` imports from deprecated code that imports non-existent module~~ ✅
2. Complete slice 751 Phase 5: CLI integration (replace deprecated imports with new service)
3. Investigate daily data time gap handling

---

Updated: 2026-01-19
