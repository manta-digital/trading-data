---
docType: review
layer: project
reviewType: slice
slice: tick-event-hypertable-schema
project: squadron
verdict: PASS
sourceDocument: project-documents/user/slices/105-slice.tick-event-hypertable-schema.md
aiModel: z-ai/glm-5
status: complete
dateCreated: 20260404
dateUpdated: 20260404
findings:
  - id: F001
    severity: pass
    category: schema-design
    summary: "Natural key design matches architecture specification"
  - id: F002
    severity: pass
    category: schema-design
    summary: "Chunk interval and space partitioning align with architecture"
  - id: F003
    severity: pass
    category: schema-design
    summary: "Compression policy matches architectural guidance"
  - id: F004
    severity: pass
    category: scope
    summary: "Trade/quote focus aligns with architecture's phased approach"
  - id: F005
    severity: pass
    category: scope
    summary: "Schema-only scope respects architectural boundaries"
  - id: F006
    severity: pass
    category: configuration
    summary: "Separate database instance handled appropriately"
  - id: F007
    severity: pass
    category: schema-design
    summary: "Logical foreign key correctly handles cross-database constraint"
  - id: F008
    severity: note
    category: schema-design
    summary: "Retention policy deferred appropriately"
---

# Review: slice — slice 105

**Verdict:** PASS
**Model:** z-ai/glm-5

## Findings

### [PASS] Natural key design matches architecture specification

The slice implements exactly the natural key specified in the architecture: `(instrument_id, timestamp, sequence_number, source)`. The architecture document states this design explicitly in the Technical Considerations section, and the slice implements it faithfully with the unique index and ON CONFLICT strategy.

### [PASS] Chunk interval and space partitioning align with architecture

The architecture specifies "1hr chunks" and "space-partitioned by `instrument_id`" for the tick hypertable. The slice implements 1-hour chunk intervals via `INTERVAL '1 hour'` and space partitioning via `add_dimension()` with 4 partitions on `instrument_id`. This matches the architecture precisely.

### [PASS] Compression policy matches architectural guidance

The architecture specifies compression should "segment by `instrument_id`, order by `timestamp, sequence_number`". The slice implements this exactly via `timescaledb.compress_segmentby = 'instrument_id'` and `timescaledb.compress_orderby = 'timestamp, sequence_number'`.

### [PASS] Trade/quote focus aligns with architecture's phased approach

The architecture explicitly states: "For the initial schema, focus on trade and quote events only — these cover the primary use cases." The slice correctly implements only trade and quote event types, with clear documentation that BBO, NBBO, depth, and status event types are out of scope until needed.

### [PASS] Schema-only scope respects architectural boundaries

The architecture states: "population happens in Initiative 120" and "do not build tick ingestion or processing in this initiative." The slice correctly scopes to migration scripts, schema constants, and validation tooling only, explicitly excluding application-level data access classes, ingestion pipeline, and CLI commands.

### [PASS] Separate database instance handled appropriately

The architecture requires "The tick hypertable should be on a separate database instance from minute data." The slice addresses this by adding `tick_db_url` to Settings (env var: `MT_TICK_DB_URL`), extending the established URL pattern. While the architecture mentioned only `market_db_url` and `timescale_db_url`, the third URL is architecturally consistent with the separate-instance requirement and follows the naming convention.

### [PASS] Logical foreign key correctly handles cross-database constraint

The slice correctly identifies that a PostgreSQL FOREIGN KEY constraint cannot span database instances. It implements a logical foreign key via application-level validation (to be done in Initiative 120) and adds a `CHECK (instrument_id > 0)` constraint for basic sanity checking. This is appropriate given the architectural requirement for separate instances.

### [NOTE] Retention policy deferred appropriately

The architecture states "retention policies are slice-level decisions." The slice explicitly excludes retention policies with the rationale that usage patterns from Initiative 120 are needed first. The 7-day compression delay is included (beneficial regardless of retention) while deferring the more consequential retention decision until real data patterns are established. This is prudent.
