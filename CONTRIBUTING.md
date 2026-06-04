# Contributing to Arrow Lake

Arrow Lake is a Python multimodal data lakehouse built on Lance, Daft, and Ray.
All contributions are welcome — bug fixes, features, docs, and test improvements.

---

## Table of Contents

1. [How to Contribute](#how-to-contribute)
2. [Development Setup](#development-setup)
3. [Code Style](#code-style)
4. [Commit Messages](#commit-messages)
5. [Pull Request Process](#pull-request-process)
6. [Testing Guidelines](#testing-guidelines)
7. [Adding New Features](#adding-new-features)
8. [Documentation Updates](#documentation-updates)

---

## How to Contribute

### Bug Reports

Open an issue with:

- **Summary** — one-line description of the problem
- **Reproduction steps** — minimal code or config to trigger the bug
- **Expected vs actual behavior**
- **Environment** — Python version, OS, relevant dependency versions

Attach logs (redacted) or stack traces when available.

### Feature Requests

Open an issue describing:

- **Motivation** — the problem you want to solve
- **Proposed solution** — high-level approach or API sketch
- **Alternatives considered** (if any)

Large features should be discussed in an issue before code is written.

### Pull Requests

See the [Pull Request Process](#pull-request-process) section below.

---

## Development Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) or pip with venv support
- Docker (for integration tests)

### Clone and Install

```bash
git clone https://github.com/<your-fork>/wits-infra-dintellihub.git
cd wits-infra-dintellihub

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"

# Install optional dependency groups as needed
pip install -e ".[fts,rag,gravitino,jwt,otel]"
```

### Pre-commit Hooks

```bash
pre-commit install
```

This runs ruff (lint + format), mypy, trailing-whitespace, and other checks on every commit.
See [`.pre-commit-config.yaml`](.pre-commit-config.yaml) for the full hook list.

### Verify Setup

```bash
# Lint passes
ruff check arrow_lake/

# Type checks pass
mypy arrow_lake/

# Tests pass
.venv/bin/python3 -m pytest tests/unit/ tests/api/ -q --tb=short
```

---

## Code Style

Arrow Lake uses **ruff** as the single linter/formatter — no separate black or isort.

| Tool | Config | Key Rules |
|------|--------|-----------|
| Ruff lint | `pyproject.toml [tool.ruff.lint]` | E, F, W, I, N, UP, B, A, SIM, RUF |
| Ruff format | `pyproject.toml [tool.ruff.format]` | 100 char line length, double quotes, space indent |
| mypy | `pyproject.toml [tool.mypy]` | strict mode, Pydantic plugin |

### Conventions

- **PEP 8** compliant via ruff
- **Type annotations** required on all public functions (enforced by `disallow_untyped_defs`)
- **`from __future__ import annotations`** in all modules
- **Line length**: 100 characters
- **Import sorting**: ruff handles this (`I` rule)
- **String quotes**: double quotes
- **Immutability**: prefer creating new objects over mutating existing ones
- **Error handling**: always handle explicitly — no silent `except: pass`

### Before Committing

```bash
# Auto-fix lint issues
ruff check --fix arrow_lake/

# Auto-format
ruff format arrow_lake/

# Verify no remaining issues
ruff check arrow_lake/
mypy arrow_lake/
```

---

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <short description>

<optional body with context>
```

**Types:**

| Type | Use Case |
|------|----------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `refactor` | Code restructuring without behavior change |
| `docs` | Documentation changes |
| `test` | Adding or updating tests |
| `chore` | Tooling, deps, config, CI |
| `perf` | Performance improvement |
| `ci` | CI/CD pipeline changes |

**Examples:**

```
feat(query): add faceted search with multi-field filters

fix(ingest): resolve chunker off-by-one in boundary detection

test(catalog): cover Gravitino lineage traversal edge cases

docs: update README with v1.5.2 features
```

Keep subject lines under 72 characters. Use a scope in parentheses when the change
is confined to one module.

---

## Pull Request Process

### Branch Naming

```
feature/<short-description>
fix/<short-description>
docs/<short-description>
```

Target branch: `master`.

### Checklist

Before requesting review, verify:

- [ ] `ruff check arrow_lake/` passes with no errors
- [ ] `ruff format --check arrow_lake/` passes
- [ ] `mypy arrow_lake/` passes
- [ ] All tests pass: `.venv/bin/python3 -m pytest tests/unit/ tests/api/ -q --tb=short`
- [ ] Coverage is >= 80% for changed files
- [ ] No hardcoded secrets or credentials
- [ ] New public functions have type annotations
- [ ] No bare `Exception` catches — use specific exception types
- [ ] Commit messages follow conventional commit format

### CI Requirements

The CI pipeline enforces:

1. Ruff lint and format
2. mypy strict type checking
3. pytest with coverage (`fail_under = 80` in `pyproject.toml`)
4. Security scan (bandit)

### Review Expectations

- PRs should be small and focused — one logical change per PR
- Include a clear description of what changed and why
- Reference related issues in the PR body (`Fixes #123`, `Part of #456`)
- Address review feedback before merging

---

## Testing Guidelines

### Running Tests

```bash
# Unit + API tests (fast, no external services)
.venv/bin/python3 -m pytest tests/unit/ tests/api/ -q --tb=short

# With coverage report
.venv/bin/python3 -m pytest tests/ --cov=arrow_lake --cov-report=term-missing

# Stop on first failure
.venv/bin/python3 -m pytest tests/unit/ tests/api/ -x -q --tb=short

# Parallel execution (uses pytest-xdist)
.venv/bin/python3 -m pytest tests/ -n auto -q --tb=short
```

### Configuration

- `asyncio_mode = auto` — no need for `@pytest.mark.asyncio` on async tests
- `testpaths = ["tests"]` — all test directories are discovered automatically
- See [`pyproject.toml`](pyproject.toml) for pytest markers

### Test Directory Layout

| Test Type | Directory | Requirement |
|-----------|----------|-------------|
| Unit | `tests/unit/` | Always — mirrors `arrow_lake/` structure |
| API | `tests/api/` | Always — tests FastAPI endpoints |
| Integration | `tests/integration/` | Requires Docker Compose |
| E2E | `tests/e2e/` | Before release |
| Smoke | `tests/smoke/` | Platform boot verification |
| Benchmark | `tests/benchmark/` | Requires local LLM |

Test files mirror source files: `tests/unit/query/test_olap.py` maps to `arrow_lake/query/olap.py`.

### TDD Workflow

1. **Write the test first** — it should fail
2. **Run the test** — confirm it fails (RED)
3. **Write minimal implementation** — just enough to pass
4. **Run the test** — confirm it passes (GREEN)
5. **Refactor** — clean up while keeping tests green
6. **Verify coverage** — 80%+ for new code

### Coverage Requirements

Minimum: **80%** (enforced by `fail_under = 80` in [`pyproject.toml`](pyproject.toml)).
The project currently maintains **90%+** across 5,300+ tests.

### Test Structure

Follow the Arrange-Act-Assert pattern:

```python
async def test_returns_empty_list_when_no_datasets_match_query():
    # Arrange
    client = AsyncClient(transport=ASGITransport(app=app))
    query = "nonexistent_dataset_xyz"

    # Act
    response = await client.get("/api/v1/search", params={"q": query})

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data["results"] == []
```

### Test Naming

Use descriptive names that explain the behavior:

```python
# Good
def test_throws_validation_error_when_chunk_size_exceeds_limit():
def test_falls_back_to_string_search_when_vector_index_unavailable():
def test_skips_empty_documents_without_raising():

# Bad
def test_search():
def test_error():
def test_it_works():
```

### Test Markers

```python
@pytest.mark.integration   # Requires Docker Compose services
@pytest.mark.e2e            # End-to-end flow
@pytest.mark.slow           # Takes > 30 seconds
@pytest.mark.benchmark      # Requires local LLM, excluded from CI
```

Skip markers in CI runs:

```bash
.venv/bin/python3 -m pytest tests/ -m "not integration and not slow and not benchmark" -q
```

---

## Adding New Features

### Architecture Overview

The project uses a **Mixin-based facade** pattern. All user-facing operations go
through `arrow_lake/__init__.py:Lake`. Internal storage access via `_get_storage()`
is an implementation detail — prefer exposing new facade methods in the appropriate
`_lake_*.py` mixin.

```
Lake class -> _LakeIngestMixin
           -> _LakeSearchMixin
           -> _LakeQueryMixin
           -> _LakeAdminMixin
           -> _LakeLineageMixin
           -> _LakeAuditMixin
           -> _LakeRAGMixin
           -> _LakeKGMixin
           -> ...
```

### Project Structure

```
arrow_lake/
  api/               # FastAPI endpoints — one router per domain
  catalog/           # Gravitino metadata, lineage, tags
  config/            # Pydantic Settings sections (27 sections)
  core/              # Circuit breaker, metrics, validation, logging
  ingest/            # Connectors, chunking, embedding, OCR
  knowledge_graph/   # HugeGraph client, Gremlin queries
  media/             # Image/video/audio processing
  ops/               # Backup/restore operations
  quality/           # Validation, dedup, masking, profiling
  query/             # Vector search, FTS, hybrid, faceted, OLAP
  rag/               # LLM pipeline, reranking, sessions
  ray_runtime/       # Distributed compute tasks
  storage/           # Lance adapter, blob store, versioning
  testing/           # Test utilities and shared fixtures
  workflow/          # Audit, retry, rollback, Argo integration
tests/               # 400+ test files, 5,325 tests
deploy/              # Docker Compose, Dockerfile, Helm chart, Nginx
configs/             # YAML config examples
flows/               # Metaflow pipeline definitions
```

### Guidelines

- **One module = one concern.** Files should be 200-400 lines, never exceed 800.
- **Use Pydantic v2 models** for all data structures and config.
- **Use structlog** for logging (not stdlib `logging`).
- **Use httpx** for HTTP calls (not `requests`).
- **Use tenacity** for retry logic.
- **Circuit breakers** from `arrow_lake.core` for external service calls.
- **Type annotate everything** — mypy strict mode is enforced.
- **Prefer immutable patterns** — create new objects rather than mutating in place.

### Adding a New API Endpoint

1. Create or extend a router in `arrow_lake/api/`
2. Define request/response Pydantic models
3. Implement the handler with async support
4. Write tests in `tests/api/`
5. Register the router in the app factory

### Adding a New Connector

1. Implement in `arrow_lake/ingest/connectors/`
2. Follow existing connector patterns (async `ingest()` method)
3. Add config section in `arrow_lake/config/`
4. Write unit tests in `tests/unit/ingest/`

### Adding a Facade Method

1. Create or extend the mixin in `arrow_lake/_lake_*.py`
2. Implement the method delegating to internal modules
3. Write unit tests mirroring the source structure
4. Update `arrow_lake/__init__.py` if the Lake class interface changes

---

## Documentation Updates

### What to Document

- Public API changes — update docstrings and relevant markdown
- New configuration options — add to config reference
- New CLI commands — update help text
- Breaking changes — update `CHANGELOG.md`

### Where

| Content | Location |
|---------|----------|
| Project overview | [`README.md`](README.md) |
| Changelog | [`CHANGELOG.md`](CHANGELOG.md) |
| Config reference | `docs/` |
| Examples | `examples/` |
| API reference | Inline docstrings (Google style) |

### Docstring Style

```python
async def search(
    query: str,
    *,
    top_k: int = 10,
    filters: FilterExpr | None = None,
) -> list[SearchResult]:
    """Execute a hybrid vector + full-text search.

    Combines vector similarity with BM25 scoring for ranked results.
    Falls back to text-only search when the vector index is unavailable.

    Args:
        query: Natural language search query.
        top_k: Maximum number of results to return.
        filters: Optional faceted filter expression.

    Returns:
        Ranked list of search results with relevance scores.

    Raises:
        SearchError: If the query engine is unreachable.
    """
    ...
```

---

## Security

- No hardcoded secrets (API keys, passwords, tokens) — use environment variables
- All user inputs validated at system boundaries
- SQL/Gremlin injection prevention: use parameterized queries
- Error messages must not leak sensitive data
- Report security issues via SECURITY.md (no public disclosure)

---

## Questions?

- Open a [GitHub Discussion](https://github.com/<org>/wits-infra-dintellihub/discussions) for general questions
- Open an issue for bugs or feature requests
- Check `docs/` and `examples/` for existing documentation and usage patterns
