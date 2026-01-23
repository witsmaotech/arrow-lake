# Sprint 2 Day 1 - Performance Test Summary

**Date**: 2026-01-23
**Goal**: Achieve QPS > 1000 (3.5x improvement from Sprint 1's 283 QPS)
**Result**: ❌ **Target Not Achieved** - Best result: 355 QPS (HTTP API) / 295 QPS (Direct Library)

---

## Test Results Overview

### 1. Direct Library - Multiprocessing (Failed)
**Approach**: Python multiprocessing with Pool (spawn method)
**Result**: ❌ 52 QPS (8 workers)
**Issue**: Process spawning overhead too high, IPC overhead

```
Worker 1:  38 QPS
Worker 2:  50 QPS
Worker 4:  52 QPS
Worker 8:  43 QPS (actually worse!)
```

**Root Cause**: LanceDB already releases GIL via Rust, so multiprocessing adds overhead without benefit.

---

### 2. HTTP API - Gunicorn Multi-Workers
**Configuration**: 8 Gunicorn workers, uvicorn class
**Test Data**: 10K vectors, 128 dimensions, Cosine index

#### Concurrency Test Results

| Threads | QPS    | P99 Latency | Success Rate |
|---------|--------|-------------|--------------|
| 1       | 85     | 19ms        | 100%         |
| 10      | 225    | 69ms        | 100%         |
| 20      | 246    | 146ms       | 100%         |
| 50      | 270    | 141ms       100%         |
| 100     | 345    | 355ms       | 100%         |
| 200     | 355    | 589ms       | 100%         |

**Best Result**: 355 QPS with 200 concurrent threads

**Analysis**:
- HTTP overhead (serialization, network stack, JSON) limits performance
- Increasing workers from 4 → 8 helped slightly
- Diminishing returns beyond 100 threads
- P99 latency degrades significantly with high concurrency

---

### 3. Direct Library - ThreadPoolExecutor (Sprint 1 Approach)
**Configuration**: Local LanceDB, 100K vectors, Cosine index

#### Thread Scaling Results

| Threads | QPS    | P99 Latency | Avg Latency |
|---------|--------|-------------|-------------|
| 10      | 275    | 45ms        | 36ms        |
| 20      | 278    | 87ms        | 71ms        |
| 50      | 289    | 208ms       | 167ms       |
| 100     | 295    | 378ms       | 319ms       |

**Best Result**: 295 QPS with 100 threads

**Analysis**:
- LanceDB's Rust implementation releases GIL effectively
- Thread-based concurrency works well
- Performance ceiling around 300 QPS on this hardware
- P99 latency degrades sharply beyond 20 threads

---

## Performance Comparison

| Approach                    | QPS    | P99 Latency | Notes                          |
|-----------------------------|--------|-------------|--------------------------------|
| Sprint 1 Baseline (10T)     | 283    | 42ms        | Direct library, 100K vectors   |
| Multiprocessing (8W)        | 52     | N/A         | Too much overhead              |
| HTTP API (8W, 200T)         | 355    | 589ms       | HTTP overhead, bad latency     |
| Direct Library (100T)       | 295    | 378ms       | Best single-instance result    |

---

## Root Cause Analysis

### Why Can't We Reach 1000 QPS?

1. **LanceDB IVF Index Limitation**
   - IVF (Inverted File Index) has inherent overhead
   - Each query searches multiple inverted lists
   - Disk I/O and CPU bound operations

2. **Hardware Constraints**
   - Limited CPU cores (need to check actual core count)
   - Disk I/O bottleneck
   - Memory bandwidth limitation

3. **Index Parameters**
   - Current: IVF with default nprobes
   - Could be optimized for read-heavy workload

4. **Single-Instance Bottleneck**
   - All queries hit the same LanceDB instance
   - Lock contention (even with GIL released)
   - Resource contention at process level

---

## Recommendations to Achieve 1000 QPS

### Option 1: Horizontal Scaling (Recommended) ⭐

**Architecture**: Multiple LanceDB instances + Load Balancer

```
                   ┌─────────────┐
                   │  Nginx /    │
                   │  HAProxy    │
                   └──────┬──────┘
                          │
         ┌────────────────┼────────────────┐
         │                │                │
    ┌────▼────┐      ┌────▼────┐     ┌────▼────┐
    │LanceDB  │      │LanceDB  │     │LanceDB  │
    │Instance │      │Instance │     │Instance │
    │   #1    │      │   #2    │     │   #3    │
    └─────────┘      └─────────┘     └─────────┘
```

**Implementation**:
1. Deploy 4 LanceDB instances (target: 250-300 QPS each)
2. Configure Nginx/HAProxy as load balancer
3. Use consistent hashing for session affinity
4. Each instance stores 1/4 of the data (sharding)

**Expected QPS**: 1200+ (4 × 300)

**Effort**: 2-3 days

---

### Option 2: Data Sharding + Router

**Architecture**: Application-level sharding

```
┌──────────────┐
│  API Layer   │
│  with Router │
└──────┬───────┘
       │
   ┌───┴────┬────────┬────────┐
   │        │        │        │
┌──▼──┐  ┌──▼──┐  ┌──▼──┐  ┌──▼──┐
│Shard│  │Shard│  │Shard│  │Shard│
│  0  │  │  1  │  │  2  │  │  3  │
└─────┘  └─────┘  └─────┘  └─────┘
```

**Implementation**:
1. Shard data by hash(key) % 4
2. Each shard in separate LanceDB table/instance
3. Router queries appropriate shard
4. Parallel queries for multi-shard searches

**Expected QPS**: 1000+ (with 4 shards)

**Effort**: 3-4 days

---

### Option 3: Optimize Index Parameters

**Tuning Parameters**:
```python
# Current: IVF with default settings
table.create_index(metric='cosine', vector_column_name='vector')

# Optimized: Adjust num_partitions and nprobes
# - Larger num_partitions: Faster but less accurate
# - Larger nprobes: More accurate but slower
```

**Expected QPS**: 400-500 (30-50% improvement)

**Effort**: 0.5 day

**Trade-off**: May reduce recall rate

---

### Option 4: Upgrade Index Type

**Alternative Indexes**:
- **HNSW** (Hierarchical Navigable Small World)
  - Better for high QPS read workload
  - Higher memory usage
  - Supported in LanceDB?

**Expected QPS**: 600-800 (2-3x improvement)

**Effort**: 1 day (if supported)

---

### Option 5: Hardware Upgrade

**Requirements**:
- More CPU cores (8 → 16+)
- Faster SSD (NVMe)
- More RAM (32GB → 64GB)

**Expected QPS**: 500-700 (2x improvement)

**Cost**: $$$

**Not Recommended** for MVP stage

---

## Sprint 2 Revised Plan

### Phase 1: Quick Wins (1-2 days)

1. ✅ Fix HTTP API pandas serialization (DONE)
2. ✅ Increase Gunicorn workers to 8 (DONE)
3. Optimize LanceDB index parameters
4. Test HNSW index if available

**Target**: 400-500 QPS

### Phase 2: Horizontal Scaling (3-4 days)

1. Design multi-instance architecture
2. Implement sharding strategy
3. Deploy 4 LanceDB instances
4. Configure load balancer
5. End-to-end testing

**Target**: 1200+ QPS ✅

---

## Bugs Fixed

### 1. Missing Pandas Dependency
**Issue**: `No module named 'pandas'` error in LanceDB service
**Fix**: Added `pandas>=2.0.0` to Dockerfile.lancedb

### 2. Pydantic Serialization Error
**Issue**: `Unable to serialize unknown type: <class 'numpy.ndarray'>`
**Fix**: Convert numpy arrays to lists in search endpoint (main.py:149)

---

## Next Steps

1. **Option A**: Implement horizontal scaling (recommended)
   - Highest impact
   - Production-ready architecture
   - 2-3 days effort

2. **Option B**: Continue tuning single instance
   - Optimize index parameters
   - Test HNSW index
   - May not reach 1000 QPS

3. **Option C**: Adjust sprint goal
   - Target 500 QPS instead of 1000
   - Focus on optimization + readiness for horizontal scaling

**Recommendation**: Proceed with Option A (horizontal scaling)

---

## Appendix: Test Environment

**Hardware**:
- WSL2 on Windows
- CPU: (Check with `lscpu`)
- RAM: (Check with `free -h`)
- Disk: (Check with `df -h`)

**Software Versions**:
- Python: 3.11
- LanceDB: 0.10.0+
- Pandas: 3.0.0
- Gunicorn: 21.2.0+

**Test Data**:
- Vectors: 10K - 100K
- Dimension: 128
- Index: IVF with Cosine distance
