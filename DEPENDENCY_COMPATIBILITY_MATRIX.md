# Dependency Compatibility Matrix

**Last Updated:** 2026-05-26
**Python Version:** 3.11.14
**Status:** Validated on current codebase (v1.5.3)

---

## Core Stack (DARMU)

| Package | Version | Constraint | Python 3.11 | Python 3.12 | Responsibility Boundary |
|---------|---------|------------|:-----------:|:-----------:|------------------------|
| daft | 0.7.8 | `==0.7.8` | ✅ | ⚠️ untested | Data transformation, multimodal DataFrame, lazy eval |
| ray[default] | 2.54.1 | `==2.54.1` | ✅ | ⚠️ untested | Distributed compute, inference parallelization, Ray Serve |
| metaflow | 2.19.22 | `==2.19.22` | ✅ | ⚠️ untested | ML workflow orchestration, user-facing pipelines |
| metaflow-ray | 0.1.4 | `==0.1.4` | ✅ | ⚠️ untested | Metaflow + Ray integration bridge |

**Overlap Resolution:**
- **Daft vs Ray Data**: Daft handles ETL/transform pipeline. Ray handles distributed inference/serving. No overlap in current usage.
- **Metaflow vs Ray**: Metaflow for ML dev workflows. Ray for production serving. Argo for K8s cron workflows.
- **Upgrade risk**: Daft and Ray both depend on PyArrow. Version pin (23.0.1) must be compatible with both.

---

## Extension Layer

| Package | Version | Constraint | Python 3.11 | Python 3.12 | Role |
|---------|---------|------------|:-----------:|:-----------:|------|
| lancedb | 0.30.2 | `==0.30.2` | ✅ | ⚠️ | Vector + FTS storage, columnar, versioned |
| pylance | ≥6.0.0 | `>=6.0.0` | ✅ | ⚠️ | Lance format bindings |
| duckdb | 1.5.2 | `==1.5.2` | ✅ | ⚠️ | OLAP analytics, metadata catalog |
| pyarrow | 23.0.1 | `==23.0.1` | ✅ | ⚠️ | Arrow IPC, shared between Daft + Lance + DuckDB |

**Critical constraint:** PyArrow 23.0.1 is shared by daft, lancedb, and duckdb. All three must be tested together before any PyArrow version bump.

---

## Application Layer

| Package | Version | Constraint | Role |
|---------|---------|------------|------|
| pydantic | ≥2.10 | `>=2.10` | Schema definitions, Settings, API models |
| pydantic-settings | ≥2.7 | `>=2.7` | Environment config management |
| fastapi | ≥0.115 | `>=0.115` | REST API framework |
| uvicorn[standard] | ≥0.34 | `>=0.34` | ASGI server |
| click | ≥8.1 | `>=8.1` | CLI framework |
| rich | ≥13.0 | `>=13.0` | CLI progress bars, formatted output |
| slowapi | ≥0.1.9 | `>=0.1.9` | Rate limiting |

---

## Infrastructure Layer

| Package | Version | Constraint | Role |
|---------|---------|------------|------|
| redis[hiredis] | 5.0–5.x | `>=5.0,<6.0` | Session, JWT blacklist, distributed semaphore |
| boto3 | ≥1.35 | `>=1.35` | MinIO / S3 interaction |
| httpx | ≥0.28 | `>=0.28` | HTTP client (LLM providers, connection pool) |
| tenacity | ≥9.0 | `>=9.0` | Retry logic with exponential backoff |
| structlog | ≥24.4 | `>=24.4` | JSON structured logging |
| prometheus-client | ≥0.21 | `>=0.21` | /metrics endpoint |

---

## Multimodal & AI Layer

| Package | Version | Constraint | Role |
|---------|---------|------------|------|
| sentence-transformers | ≥3.3 | `>=3.3` | Text embedding (HuggingFace local) |
| Pillow | ≥10.4 | `>=10.4` | Image processing |
| av | ≥12.0 | `>=12.0` | Video keyframe extraction (PyAV) |

---

## Upgrade Risk Assessment

### 🔴 High Risk (breaking changes likely)

| Package | Risk | Impact | Recommendation |
|---------|------|--------|---------------|
| lancedb | API changes every minor version | Storage layer breaks | Pin exact version, test migration before upgrade |
| daft | PyArrow coupling | DataFrame pipeline breaks | Test with PyArrow compatibility matrix |
| ray | Python 3.12 support incomplete | Distributed features may fail | Stay on 3.11 until Ray officially supports 3.12 |

### 🟡 Medium Risk

| Package | Risk | Impact | Recommendation |
|---------|------|--------|---------------|
| pyarrow | Shared dependency between 3 packages | Cascade failure | Any PyArrow change requires full daft+lancedb+duckdb test |
| pydantic | v1→v2 migration already done | Residual v1 patterns | Audit for `@validator` usage |
| duckdb | API stability generally good | OLAP breaks | Minor version bumps usually safe |

### 🟢 Low Risk

| Package | Risk | Impact | Recommendation |
|---------|------|--------|---------------|
| fastapi | Stable, well-maintained | API layer | Safe to upgrade within major version |
| click | Very stable | CLI | Safe to upgrade |
| structlog | Stable | Logging | Safe to upgrade |
| boto3 | Frequent updates, backward compatible | S3 interaction | Safe to upgrade |

---

## Tested Combinations

| Python | daft | ray | metaflow | lancedb | duckdb | pyarrow | Status |
|--------|------|-----|----------|---------|--------|---------|--------|
| 3.11.14 | 0.7.8 | 2.54.1 | 2.19.22 | 0.30.2 | 1.5.2 | 23.0.1 | ✅ Production |

---

## Upgrade Checklist

Before upgrading any core dependency:

1. [ ] Check this matrix for cascade dependencies
2. [ ] Run full test suite (`pytest -q --tb=line`)
3. [ ] Verify PyArrow compatibility if touching daft/lancedb/duckdb
4. [ ] Test on both Python 3.11 and 3.12 (if supported)
5. [ ] Update this document with new tested combination
