---
docType: review
layer: project
reviewType: slice
slice: trading-calendar-integration
project: squadron
verdict: PASS
sourceDocument: project-documents/user/slices/104-slice.trading-calendar-integration.md
aiModel: z-ai/glm-5
status: complete
dateCreated: 20260403
dateUpdated: 20260403
---

# Review: slice — slice 104

**Verdict:** PASS
**Model:** z-ai/glm-5

## Findings

### [PASS] Correct use of psycopg3 and ConnectionPool

The slice specifies psycopg3 with `ConnectionPool(conninfo, min_size=1, max_size=3)`, directly aligning with the architecture's goal to "Consolidate on psycopg3" and the principle that "all modules use psycopg3 with `psycopg_pool.ConnectionPool`."

### [PASS] Lazy initialization addresses architecture-recommended fix

The architecture explicitly identifies the hard-fail-on-construct issue: "The psycopg3 rewrite of this module should also make initialization more defensive (lazy-load or explicit `connect()` method rather than fail-on-construct)." The slice directly addresses this with `_ensure_loaded()` pattern, implementing lazy loading exactly as recommended.

### [PASS] Correct fix for @lru_cache cross-instance pollution

The architecture identifies that the existing `TradingCalendar` "Uses `@lru_cache` on instance methods (causes cross-instance cache pollution and prevents GC; must be fixed in psycopg3 rewrite)." The slice correctly replaces this with per-instance dict caching, consistent with the InstrumentRegistry pattern from slice 103.

### [PASS] Connection URL pattern matches architecture requirement

The architecture specifies: "No module accepts a `db_config` dict or individual connection params — all take a connection URL string." The slice correctly uses `TradingCalendar.__init__(calendar_id, conninfo)` where `conninfo` is a URL string sourced from `Settings.timescale_db_url`.

### [PASS] Scope correctly bounded to calendar integration

The slice correctly limits scope to "Calendar integration" as defined in the architecture's "Anticipated Slices" section. Out-of-scope items (IDataService protocol, data quality validation, tick storage) are properly deferred to Initiative 140 and 120, avoiding scope creep.

### [PASS] Dependency chain correctly specified

The slice correctly depends on slice 102 for `trading_calendars` and `trading_holidays` tables, matching the architecture's ordering constraint that "schema creation and seed data must be a single atomic slice that completes before any code instantiates `TradingCalendar`."

### [PASS] Testing approach follows architecture principles

The architecture emphasizes "Real data over abstractions" with tests that "skip when DB is unavailable, but design tests to run against real data when present." The slice specifies "Unit tests with mocked DB connections" and "Integration tests against real TimescaleDB (skip when unavailable)", matching this principle.

### [PASS] ZoneInfo migration aligns with modernization goals

Replacing `pytz` with `zoneinfo.ZoneInfo` (stdlib since 3.9) is a sound technical decision that eliminates a legacy dependency and fixes DST handling issues. The slice correctly notes to check for other `pytz` usage before removing the dependency entirely.
