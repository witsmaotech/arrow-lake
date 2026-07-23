# Arrow Lake developer tasks.
# Usage: `make <target>`. Requires `.venv/bin/python3` (project convention).

PYTHON ?= .venv/bin/python3

# Templates live under arrow_lake/knowledge_graph/templates/*.yaml
TEMPLATES_DIR ?= arrow_lake/knowledge_graph/templates

.PHONY: validate-templates help

help:
	@echo "Arrow Lake developer tasks:"
	@echo "  validate-templates  Validate KG YAML templates (L1-L4 checklist, 0 ERROR required)"

## validate-templates: regression gate for KG template strictness (v1.9.2 批3).
## Runs scripts/validate_templates.py over the project templates dir; exits
## non-zero on any ERROR so CI/local pre-commit can block template drift.
validate-templates:
	$(PYTHON) arrow_lake/knowledge_graph/scripts/validate_templates.py $(TEMPLATES_DIR)
