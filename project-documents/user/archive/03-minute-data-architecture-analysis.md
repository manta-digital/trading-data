---
layer: project
docType: analysis
feature: minute-data-architecture
project: trading
triggeredBy: architecture-exploration
sourceDocument: private/features/02-lld.minute-data.md
dependencies: [database-performance, existing-csv-system]
affects: [market-data-pipeline, minutedataservice, marketdb, timescaledb]
complexity: medium-high
lastUpdated: 2025-08-25
---

# Minute Data Architecture Analysis & Hardware Planning

## Overview

Comprehensive analysis comparing three minute data storage architectures with detailed hardware requirements and performance projections. This analysis informed the decision-making process for implementing minute-level OHLCV data collection at scale.

## Architecture Comparison Summary

### 📊 **Final Comparison: Top 3 Architectures**

| Feature | **Hybrid (Base)** | **All-CSV** | **TimescaleDB** |
|---------|-------------------|-------------|-----------------|
| **Write Performance** | 14-25k rows/sec | 25-50k rows/sec | 15k+ rows/sec |
| **Query Performance** | <5s (1-day) | <3s (1-day) | <1s (1-day) |
| **Aggregated Queries** | Manual/Limited | Limited | **Automatic/Real-time** |
| **Storage Cost** | Medium | **Lowest** | Medium |
| **Storage Size (SP500, 20yr)** | ~72 GB | **~27 GB** | **~4.5 GB** |
| **Implementation Complexity** | **High** | Medium | Medium-High |
| **Maintenance Overhead** | **High** (2 systems) | Low | Medium |
| **Data Accessibility** | Mixed | **Direct files** | SQL only |
| **Backup Simplicity** | Complex | **File copy** | PostgreSQL |
| **Migration Flexibility** | Medium | **Highest** | PostgreSQL-bound |
| **Real-time Analytics** | Manual | Limited | **Built-in** |
| **Compression Efficiency** | 60% | 75% | **95%** |
| **Operational Complexity** | **High** | **Low** | Medium |

## Detailed Architecture Analysis

### Hybrid Solution (Base) - Eliminated
**Why eliminated:**
- **Complexity**: Two storage systems (database + files) create operational burden
- **Data Sync Issues**: Risk of inconsistencies between storage tiers
- **Testing Complexity**: Must validate both storage paths and transitions
- **Development Overhead**: Maintaining both backends increases development time

### All-CSV Solution
**Strengths:**
- **Simplicity**: Single storage system, easy to understand and debug
- **Cost**: Lowest storage and operational costs
- **Speed**: Fastest write performance (25-50k rows/sec)
- **Accessibility**: Perfect "grab a file" workflow for external analysis
- **Portability**: Easy migration, cloud storage integration
- **Human-readable**: CSV format excellent for troubleshooting

**Weaknesses:**
- **No ACID Properties**: Risk of partial writes during failures
- **Limited Query Capabilities**: No SQL joins, complex aggregations difficult
- **Concurrency Issues**: File locking challenges with concurrent access
- **No Built-in Aggregations**: Manual calculation of OHLCV bars required
- **Cache Complexity**: Must build sophisticated caching for performance

### TimescaleDB Solution - RECOMMENDED
**Strengths:**
- **Purpose-Built**: Designed specifically for time-series financial data
- **Automatic Aggregations**: Continuous aggregations eliminate manual work
- **Compression**: Best-in-class 95% compression ratios
- **Performance**: Fastest query performance, especially for analytics
- **Built-in Features**: Gap filling, retention policies, monitoring
- **PostgreSQL Ecosystem**: Full SQL capabilities with time-series extensions

**Weaknesses:**
- **Specialized Knowledge**: Requires TimescaleDB-specific expertise
- **Resource Requirements**: Higher memory/CPU requirements
- **Debugging**: Compressed chunks can make data inspection difficult

## Hardware Analysis: 12th Gen i7 + 64GB + 4TB

### Performance Projections

#### **System Specifications**
- **CPU**: 12th Gen i7 (8-16 cores)
- **RAM**: 64GB DDR4/DDR5
- **Storage**: 4TB NVMe SSD
- **Usage**: Dedicated system (no resource contention)

#### **TimescaleDB Performance with Your Hardware**

**Write Performance:**
```
Expected: 20k-30k rows/sec bulk writes
- 12-core parallel processing capability
- 64GB RAM buffer = massive write batches
- Dedicated system = zero resource interference
```

**Query Performance:**
```
1-day minute data range: <500ms (vs <1s target)
Aggregated queries (5min/15min bars): <100ms (vs <500ms target)
Complex multi-symbol analytics: <2s
Background compression: No query impact
```

**Storage Utilization:**
```
SP500 (20 years compressed): ~4.5GB
Russell 3000 (20 years compressed): ~27GB
Available capacity: 4TB = 148x Russell 3000 storage
Overhead for exports/backups: 500GB
Net capacity utilization: <1% for SP500, ~5% for Russell 3000
```

### Optimized Configuration

#### **PostgreSQL/TimescaleDB Settings**
```ini
# Memory settings (64GB RAM optimized)
shared_buffers = 16GB              # 25% of total RAM
effective_cache_size = 48GB        # 75% of total RAM
work_mem = 512MB                   # Higher for time-series aggregations
maintenance_work_mem = 4GB         # Bulk operations and compression
wal_buffers = 128MB                # Large WAL buffer

# CPU settings (12+ cores)
max_worker_processes = 16          # Matches core count
max_parallel_workers = 12          # Reserve 4 cores for OS
max_parallel_workers_per_gather = 6  # Aggressive parallel queries
max_parallel_maintenance_workers = 4 # Parallel compression jobs

# TimescaleDB optimizations
timescaledb.max_background_workers = 8
timescaledb.compress = on
timescaledb.compress_segmentby = 'symbol'
timescaledb.compress_orderby = 'time DESC'
```

#### **Storage Layout Recommendation**
```
/data/postgresql/    # 3TB - Main TimescaleDB storage
/data/exports/       # 500GB - CSV export staging
/data/backups/       # 500GB - Local backups (before Glacier)

File system: ext4 or XFS with noatime,data=writeback
```

#### **OS-Level Optimizations**
```bash
# Memory management
echo 'vm.nr_hugepages = 8192' >> /etc/sysctl.conf  # 16GB huge pages
echo 'vm.swappiness = 1' >> /etc/sysctl.conf

# I/O optimization for NVMe
echo mq-deadline > /sys/block/nvme0n1/queue/scheduler

# Network and file system limits
echo 'net.core.rmem_max = 134217728' >> /etc/sysctl.conf
echo 'fs.file-max = 1000000' >> /etc/sysctl.conf
```

## Performance Benchmarks

### Collection Performance Estimates
```
Single symbol (20yr historical): 2-4 hours
SP500 complete historical: 3-5 days
Russell 3000 complete: 2-3 weeks (background process)
Real-time symbol updates: <1 minute per symbol
```

### Query Performance Examples
```sql
-- Expected performance on your hardware:

-- 1 day minute data: ~100ms
SELECT * FROM minute_ohlcv 
WHERE symbol='ES' AND time >= NOW() - INTERVAL '1 day';

-- 1 month 5-minute bars: ~50ms
SELECT * FROM minute_5min_ohlcv 
WHERE symbol='ES' AND time_bucket >= NOW() - INTERVAL '1 month';

-- Multi-symbol analytics: ~500ms
SELECT symbol, AVG(volume), MAX(high-low) as daily_range
FROM minute_daily_ohlcv 
WHERE time_bucket >= NOW() - INTERVAL '1 year'
GROUP BY symbol;
```

## Cost Analysis

### Hardware Investment
```
12th Gen i7 CPU: ~$400
64GB DDR4/DDR5: ~$200
4TB NVMe SSD: ~$300
Motherboard/Case/PSU: ~$300
Total hardware cost: ~$1,200
```

### Operational Costs
```
Monthly electricity (24/7): ~$20-40
AWS Glacier backup storage: ~$1-5
Total monthly operational: ~$25-50
Annual operational: ~$300-600
```

### Cloud Alternative Cost Comparison
```
Equivalent AWS RDS TimescaleDB:
- db.r6i.2xlarge (8 vCPU, 64GB RAM): ~$1,200/month
- 4TB storage: ~$400/month
- Total cloud cost: ~$1,600/month = $19,200/year

ROI Analysis:
- Hardware investment: $1,200 (one-time)
- Annual operational: ~$500
- Total first-year cost: ~$1,700
- Cloud savings: ~$17,500/year
- Payback period: <1 month
```

## Implementation Strategy

### TimescaleDB Enhanced with CSV Integration

**Core Architecture:**
- TimescaleDB for primary storage and real-time analytics
- Automated CSV export capabilities for external tool integration
- High-performance caching layer for frequently accessed data
- Standard PostgreSQL backup + Parquet archive strategy

**Best of Both Worlds Features:**
```python
# Real-time aggregations (TimescaleDB strength)
data = await service.get_minute_data('ES', start, end, aggregation='5min')

# File export when needed (CSV strength)  
await service.export_to_csv_files('ES', start, end, '/exports/')

# Result: Performance + accessibility
```

### Implementation Phases

**Phase 1: Core TimescaleDB (Week 1-2)**
- Set up TimescaleDB with hypertables
- Implement high-performance bulk writes
- Configure continuous aggregations
- Validate performance benchmarks

**Phase 2: CSV Integration (Week 3)**
- Add automated CSV export functionality
- Implement background Parquet archival
- Create "grab a file" access patterns

**Phase 3: Production Optimization (Week 4)**
- Advanced caching implementation
- Automated compression and retention policies
- Comprehensive monitoring and alerting
- Performance tuning and optimization

## Risk Assessment

### Technical Risks
- **TimescaleDB Learning Curve**: Mitigation through documentation and support
- **Hardware Single Point of Failure**: Mitigation through backup strategies
- **Data Corruption**: Mitigation through WAL logging and regular backups

### Operational Risks
- **Power/Network Outages**: Mitigation through UPS and redundant connections
- **Storage Capacity**: Mitigation through monitoring and alerting (low risk with 4TB)

## Success Criteria

### Performance Targets
- **Write Performance**: >15k rows/sec sustained
- **Query Performance**: <1s for daily ranges, <500ms for aggregated queries
- **System Uptime**: >99.9% availability
- **Compression Ratio**: >90% for historical data

### Operational Targets
- **Backup Success Rate**: 100% daily backup completion
- **Recovery Time**: <2 hours for complete system recovery
- **Monitoring Coverage**: All critical metrics tracked and alerted

## Conclusion and Recommendation

**Final Recommendation: TimescaleDB Enhanced**

Given the analysis, TimescaleDB with CSV integration provides the optimal balance of:
- **Performance**: Sub-second queries with automatic aggregations
- **Cost Efficiency**: 95% compression reduces storage by 20x vs uncompressed
- **Operational Simplicity**: Single primary system with export capabilities
- **Future Readiness**: Built for financial time-series, scales naturally
- **Hardware Utilization**: Excellent match for available hardware specifications

The hardware specifications (12th Gen i7, 64GB RAM, 4TB storage) are ideal for this workload and provide significant headroom for future expansion. The system will deliver professional-grade performance at a fraction of cloud costs while maintaining the flexibility to export data when needed for external analysis tools.

---

**Next Steps:**
1. Proceed with TimescaleDB implementation
2. Configure hardware with optimized settings
3. Implement phased rollout as outlined
4. Monitor performance and optimize as needed

**Decision Rationale:**
The continuous aggregation capabilities alone justify the choice of TimescaleDB over pure CSV solutions. The ability to instantly query 5-minute, 15-minute, and hourly bars without recalculation provides significant operational advantages for trading system development and backtesting workflows.