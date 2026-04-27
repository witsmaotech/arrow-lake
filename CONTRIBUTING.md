# Contributing to Arrow Lake

## Development Setup

```bash
# Clone and install
git clone <repo-url>
cd arrow-lake
pip install -e ".[dev]"

# Run tests
pytest tests/unit/ --tb=short -q
```

## Code Standards

- **Python 3.11+** with `from __future__ import annotations`
- **Formatting**: `ruff check --fix` (no separate formatter)
- **Type hints**: Required on all public function signatures
- **Imports**: `ruff` isort-compatible — use `ruff check` to validate
- **Style**: Follow PEP 8; immutable patterns preferred; no deep nesting (>4 levels)

## Architecture

The project uses a **Mixin-based facade** pattern:

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

All user-facing operations go through `arrow_lake/__init__.py:Lake`. Internal
storage access via `_get_storage()` is an implementation detail — prefer
exposing new facade methods in the appropriate `_lake_*.py` mixin.

## Git Workflow

- **Branch**: feature/`<short-description>`
- **Commits**: conventional commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`)
- **PR**: Target `master`, include test plan, let CI pass before requesting review

## Testing

Minimum 80% coverage (`fail_under = 80` in pyproject.toml).

| Test Type | Directory | When |
|-----------|----------|------|
| Unit | `tests/unit/` | Always |
| Integration | `tests/integration/` | Requires Docker |
| API | `tests/api/` | After unit pass |
| E2E | `tests/e2e/` | Before release |

### Test Naming

- `test_<method>_<scenario>` for unit tests
- Test files mirror source: `tests/unit/query/test_olap.py` <-> `arrow_lake/query/olap.py`

## Security

- No hardcoded secrets (API keys, passwords, tokens)
- All user inputs validated at system boundaries
- SQL injection prevention: use parameterized queries
- Report security issues via SECURITY.md (no public disclosure)

## Pull Request Checklist

- [ ] `ruff check arrow_lake/` passes
- [ ] `pytest tests/unit/ --tb=short -q` passes (no new failures)
- [ ] No `_get_storage()` in examples (use facade methods)
- [ ] No bare `Exception` (use specific exception types)
- [ ] No `assert` in examples (use explicit checks)
