---
layer: project
docType: analysis
date: 2025-01-22
purpose: Comprehensive project state analysis for minute data infrastructure
---

# Trading Project State Analysis - 2025-01-22

## Executive Summary

The trading project has **multiple overlapping and potentially conflicting slice definitions** for minute data infrastructure. There are inconsistencies in numbering systems, completion status, and architectural approaches that need resolution before proceeding.

**Key Finding:** We have two distinct architectural approaches competing:
1. **Legacy approach** (slices 020, 025): Working implementation, but marked as having architectural issues
2. **New approach** (slices 750-752): Clean architecture redesign, not yet started

**Recommendation:** Clarify which approach to follow before proceeding with implementation work.

---

## Slice Inventory

### Active Slices (by number)

#### 020-slice.minute-data.md
- **Status:** `in-progress` (Phase 3)
- **Type:** TimescaleDB implementation
- **Dependencies:** database-performance, timescaledb-setup, postgresql, alphavantage-api
- **Last Updated:** 2025-09-16
- **Current State:**
  - ✅ Phase 1: Infrastructure Setup COMPLETE
  - ✅ Phase 2: Data Collection Integration COMPLETE
  - 🔄 Phase 3: CSV Export and Service Integration IN PROGRESS
  - ⏳ Phase 4: Production Deployment PENDING
- **Key Achievement:** Write performance >15k rows/sec, query <500ms achieved
- **Notes:** Uses older numbering system (02 → 020)

#### 025-slice.minute-acquisition.md
- **Status:** `in-progress` (design phase)
- **Type:** Large-scale scheduled data collection orchestration
- **Dependencies:** 020-slice.minute-data, alphavantage-api, timescaledb-setup
- **Last Updated:** 2025-09-17
- **Current State:** High-level and low-level design complete, no implementation started
- **Key Components Designed:**
  - CLI integration (extend ohlc.sh or create minute.sh)
  - Symbol list management (SP500, R2000, etc.)
  - Batch collection orchestration
  - Rate limiting and progress tracking
  - Basic data integrity validation
- **Notes:** References slice 030-slice.data-integrity (not yet created)

#### 750-slice.minute-foundation-cleanup.md
- **Status:** `not-started`
- **Type:** Foundation infrastructure and cleanup
- **Dependencies:** None (starts fresh)
- **Last Updated:** 2025-09-30
- **Purpose:**
  - Remove deprecated code from slice 025
  - Build foundational infrastructure (instrument registry, trading calendars, adjustment policies)
- **Key Insight:** This slice **deprecates files from slice 025** that don't align with new architecture
- **Phases:**
  - Phase 1: Cleanup (1/5 effort)
  - Phase 2: Database Foundation (2/5 effort)
  - Phase 3: Python Modules (3/5 effort)
  - Phase 4: Data Validation (1/5 effort)
- **Overall Effort:** 3/5

#### 751-slice.minute-historical-core.md
- **Status:** `not-started-needs-review`
- **Type:** Core service implementation with clean architecture
- **Dependencies:** 750-slice.minute-foundation-cleanup
- **Last Updated:** 2025-10-01
- **Purpose:** Implement HistoricalMinuteService with provider abstraction
- **Key Design Principles:**
  - Clean async/sync boundaries
  - IDataService base interface for standardization
  - Provider abstraction (AlphaVantageMinuteProvider)
  - Reuse TimescaleMinuteDataDB storage layer
- **What it excludes:** Quality validation, gap detection (deferred to slice 752)

#### 752-slice.minute-data-quality.md
- **Status:** `not-started-needs-review`
- **Type:** Data quality and validation framework
- **Dependencies:** 750, 751
- **Last Updated:** 2025-10-01
- **Purpose:**
  - Idempotent ingestion (staging → validate → upsert)
  - Calendar-aware gap detection
  - Advisory validation against daily data
  - Data lineage tracking

---

## Task File Inventory

### Completed Tasks
- **800-tasks.timescale_minute_db.md**: TimescaleDB sync conversion ✅ COMPLETE (2025-01-22)
- **802-tasks.fix-timescale-pylance-errors.md**: Pylance type errors ✅ COMPLETE (2025-01-22)

### Pending Tasks
- **801-tasks.update-timescale-tests.md**: Test updates for sync implementation ⏳ NOT STARTED
  - Blocked by: Need to update 28 test methods from async to sync
  - Dependency: 800-tasks.timescale_minute_db (complete)

### Legacy Tasks (May be outdated)
- **020-tasks.minute-data.md**: Tasks for slice 020
- **025-tasks.minute-acquisition-1.md**: Tasks for slice 025
- **025-tasks.minute-acquisition-2.md**: Additional tasks for slice 025
- **010-analysis.trading-20250822.md**: Old analysis from August

---

## Architectural Conflicts

### Conflict 1: Slice 025 vs Slice 750

**Slice 025 (in-progress design):**
- Builds on top of slice 020 infrastructure
- Implements orchestration for large-scale collection
- Contains files that slice 750 wants to deprecate
- No actual implementation yet (still in design phase)

**Slice 750 (not-started):**
- Explicitly states it will **deprecate** code from slice 025
- Lists these files to be removed:
  - `manta_trading/market/timescale_minute_service.py`
  - `manta_trading/market/minutedatacoverage.py`
  - `manta_trading/market/minutedatamanager.py`
  - `manta_trading/market/minutedatabackfill.py`
  - `manta_trading/market/minutedataservice.py`
  - `manta_trading/market/minute_command_processor.py`
  - `manta_trading/market/minute_acquisition_service.py`
- Problem: If these files don't exist yet (slice 025 is still design phase), what is being deprecated?

**Question:** Does slice 025 implementation code already exist? Or was it partially implemented and caused the architectural issues mentioned in slice 750?

### Conflict 2: Numbering System Inconsistency

**Old system:** 02, 025 (with sliceIndex field)
**New system:** 750, 751, 752 (with item field)

Both systems coexist in the project. The old system slices (020, 025) are marked `in-progress`, while new system slices (750+) are `not-started`.

### Conflict 3: Async/Sync Architecture

**Slice 025 design:** Uses async patterns throughout (async/await, asyncio)
**Slice 750 foundation:** Not specified
**Slice 751 core service:** Explicitly defines "clean async/sync boundaries"
**Recent work (task 800):** Just converted TimescaleMinuteDataDB from async to **sync**

**Implication:** The async→sync conversion suggests we're moving away from async patterns, which aligns with slice 751's "clean boundaries" approach but conflicts with slice 025's async design.

---

## Code State Analysis

### Existing Code (Known)
Based on task 800 completion:
- ✅ `manta_trading/market/timescale_minute_db.py` - Exists, recently converted to sync
- ✅ `manta_trading/market/config.py` - Exists (mentioned as "to keep" in slice 750)
- ✅ `manta_trading/market/timescale_monitoring.py` - Exists (mentioned as "to keep")

### Files Targeted for Deprecation (VERIFIED - ALL EXIST)
Files mentioned in slice 750 "to remove" list - **verification complete:**
- ✅ `manta_trading/market/timescale_minute_service.py` - EXISTS
- ✅ `manta_trading/market/minutedatacoverage.py` - EXISTS
- ✅ `manta_trading/market/minutedatamanager.py` - EXISTS
- ✅ `manta_trading/market/minutedatabackfill.py` - EXISTS
- ✅ `manta_trading/market/minutedataservice.py` - EXISTS
- ✅ `manta_trading/market/minute_command_processor.py` - EXISTS
- ✅ `manta_trading/market/minute_acquisition_service.py` - EXISTS

**Finding:** All 7 files targeted for deprecation in slice 750 currently exist in the codebase.

**Additional file found:**
- ✅ `manta_trading/market/timescale_minute_coverage.py` - EXISTS (not mentioned in slice 750)

**Implication:** Slice 025 was partially or fully implemented, and slice 750 was created specifically to deprecate this problematic implementation and start fresh with clean architecture.

---

## Database State

### Confirmed Tables
- `minute_ohlcv` - Hypertable with compression and continuous aggregations (from slice 020)
- Continuous aggregation views (5min, 15min, hourly, daily, weekly, monthly)

### Proposed Tables (Not Yet Created)
From slice 750:
- `instruments`
- `provider_symbol_mapping`
- `trading_calendars`
- `trading_holidays`
- `trading_sessions`

From slice 025:
- `minute_collection_jobs`
- `minute_collection_events`
- `symbol_lists`

From slice 752:
- `ingest_runs` (lineage tracking)
- `minute_ohlcv_staging` (staging pattern)

**Note:** Multiple overlapping designs for job tracking and metadata.

---

## Recommendations

### Immediate Actions Required

1. **Verify Code State**
   - Run file listing to determine which files from the "to remove" list actually exist
   - This will clarify whether slice 025 was partially implemented

2. **Resolve Architectural Direction**
   - **Option A: Continue with 020/025 approach**
     - Complete slice 025 implementation
     - Address architectural issues incrementally
     - Faster to production but technical debt

   - **Option B: Adopt 750/751/752 approach**
     - Start fresh with clean architecture
     - Deprecate problematic code from 025 (if it exists)
     - Longer timeline but better foundation

   - **Option C: Hybrid approach**
     - Keep slice 020 infrastructure (proven effective)
     - Skip slice 025 implementation
     - Implement 750/751/752 on top of 020

3. **Update Documentation**
   - Mark slices as deprecated/superseded where appropriate
   - Update status fields to reflect actual state
   - Consolidate task files (many may be outdated)

4. **Resolve Numbering System**
   - Decide on single numbering system going forward
   - Either migrate 020/025 to new format or continue old system

### Low-Stress Next Steps

Given your stress level and desire for calm progress, I recommend:

1. **First: Verify what exists** (low stress, pure investigation)
   - List files in `manta_trading/market/` directory
   - Read and understand current state
   - No changes, just understanding

2. **Second: Discuss and decide** (collaborative, no pressure)
   - Review findings together
   - Choose architectural direction
   - Update slice status to reflect decisions

3. **Third: Clean slate start** (clear path forward)
   - Either begin slice 750 Phase 1 (simple file cleanup)
   - Or continue slice 020 Phase 3 (CSV export)
   - Single clear objective, no ambiguity

---

## Key Questions for Project Manager

1. **Were slice 025 files ever implemented?**
   - ✅ **ANSWERED:** Yes, all 7 files exist and were implemented
   - **Implication:** Slice 750 cleanup phase IS needed

2. **Which architectural approach should we follow?**
   - Legacy (020/025) or New (750/751/752)?
   - **Context:** The 750+ approach was created specifically because 025 had architectural issues

3. **What is the priority?**
   - Speed to production (finish 020/025 despite issues)?
   - Or clean foundation for long-term (750/751/752)?

4. **Should we complete task 801** (test updates)?
   - This is a clear, contained task
   - Completes the sync conversion work
   - Low-stress, well-defined
   - Can be done regardless of architectural decision

---

## Current Blocker

**Cannot proceed with implementation until architectural direction is clarified.**

We have three possible paths forward:
- Path A: Complete slice 020 Phase 3 (CSV export)
- Path B: Start slice 750 Phase 1 (cleanup and foundation)
- Path C: Complete task 801 (test updates - maintenance work)

Path C is the safest, most well-defined option for low-stress progress right now.

---

**Document Status:** Analysis complete, awaiting Project Manager decisions.
