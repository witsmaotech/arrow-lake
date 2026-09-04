# 21 · Ontology, Contracts & Objects: the Definition Layer

> Version baseline: v1.11.0–v1.11.2 (MS1 ontology formalization / MS2 semantic layer / MS3 decisions & actions), calibrated to v1.11.4. Everything below is copy-runnable; sample outputs come from real runs (demo_gas / the wb_flow pilot). Console entry: the **Ontology & Semantics** group — modeling workbench / ontology & rules / data contracts / object browser.

The definition layer answers three questions: **what the data should look like** (contracts), **what counts as business-correct** (rules), and **which business object a row is** (identity & alignment). Chapter 20's quality pipeline consumes exactly this layer: completeness reads the contract, assessment reads the rules, golden sets align by object.

```text
Modeling workbench (form) ──→ contract YAML (structure, version chain)
       │                            │
       ├──→ extraction-template skeleton   ├──→ ontology rules (5-way, draft→active)
       └──→ LS labeling config             └──→ ontology gate at KG-build finish (shadow/enforce)
                                           ↓
Object browser (Object Set query: identity / attributes / lifecycle / KG neighbors / rules)
                                           ↓
Decision console (assess, optionally recorded) → action execution (controlled writes)
```

Prereq: an ADMIN personal token (`X-API-Key`).

---

## ① Contracts — structure with a version chain

A contract is a machine-checkable declaration of per-table column rules (required/enum/range/pattern); its scope must equal the dataset name. Saving enters a version chain; identical content hashes skip:

```bash
curl -X PUT "http://127.0.0.1:8000/api/v1/contracts/wb_flow" \
  -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" -d '{
  "contract_yaml": "dataset: wb_flow\ntables:\n  valves:\n    object_class: valve\n    lifecycle: {column: status, states: [normal, fault, repair], initial: normal}\n    identifier: {column: valve_id, pattern: \"WB.V.{n:[0-9]+}\"}\n    columns:\n      - {name: valve_id, required: true}\n      - {name: pressure, label: upstream pressure, unit: kPa, type: double, range: [0, 1000]}\nreferences: []\n"}'
# → {"scope":"wb_flow","version":1,"created":true,"diff":null}
```

```bash
curl -H "X-API-Key: $TOKEN" "http://127.0.0.1:8000/api/v1/contracts/wb_flow/versions"   # chain
curl -H "X-API-Key: $TOKEN" "http://127.0.0.1:8000/api/v1/contracts/wb_flow/diff"       # structured diff
```

Notes: `lifecycle` declares the state column (consumed by the action middleware for transitions and preconditions); `identifier.pattern` named groups like `{n:[0-9]+}` parse into identity components on object queries; `label`/`unit` are the semantic attribute vocabulary. The console **modeling workbench** designs all of this in a form with three live previews (contract YAML / LS labeling config / extraction-template skeleton) and a validate-only button (`POST /contracts/parse`, no persistence).

## ② Ontology rules — registry, state machine, gate

Rules are consumed by the MS3 evaluation engine; the five-way `rule_type` (validation/computation/derivation/transformation/risk_control) carries governance semantics:

```bash
curl -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"rule_id":"WB.FLOW.R001","scope":"wb_flow","condition_expr":"pressure > 800",
       "conclusion":"overpressure alarm","source_ref":"ops manual v2 §3","rule_type":"risk_control"}' \
  http://127.0.0.1:8000/api/v1/ontology/rules
# → {"success":true,"data":{"id":16,"rule_id":"WB.FLOW.R001",...,"status":"draft"}}

# State machine: draft → active (only active rules evaluate; retired = soft-off, kept for audit)
curl -X POST -H "X-API-Key: $TOKEN" \
  "http://127.0.0.1:8000/api/v1/ontology/rules/WB.FLOW.R001/transition?to_status=active"
```

`condition_expr` uses a controlled predicate DSL (hand-written tokenizer + frozen AST, no eval; shaped like `target.pressure >= 3000`). The other ontology route comes from **extraction templates**: a template's `ontology:` section is formalized into SHACL, and KG-build finish runs the **ontology gate** — shadow (default, count only) or enforce (violations flip the build FAILED) — with version chains snapshotting Turtle + structured diffs:

```bash
curl -H "X-API-Key: $TOKEN" "http://127.0.0.1:8000/api/v1/kg/build/{task_id}/status"
# → {"ontology": {"mode": "shadow", "outcome": "pass", "violations": [], ...}}
```

## ③ Object browser — Object Set queries

Rows become business objects: identity components, label/unit attributes, lifecycle state, cross-table `_links` (cardinality/kind), optional KG neighbors and matching rules — all on the exact /query/olap security path (row/column ACLs apply):

```bash
curl -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"dataset":"demo_gas","object_type":"segments","limit":1,"include_kg":true}' \
  http://127.0.0.1:8000/api/v1/objects/query
```

```json
{"objects": [{
  "object_id": "GAS.SEG.1",
  "identifier": {"matched": true, "components": {"n": "1"}},
  "attributes": [{"column":"pressure","label":"pressure","value":100,"unit":"kPa"}],
  "lifecycle_state": "in-service",
  "_kg": {"matched": true, "neighbors": [...]},
  "_rules": [{"rule_id": "...", "conclusion": "..."}]
}]}
```

**Semantic alignment** makes cross-source same-caliber possible: affine unit conversions (MPa→kPa, temperature offsets) apply as SQL projections in the query view, so the same physical quantity entering through two source systems reads out as the same object with the same attribute in the same unit. Source-system ID → object ID mappings are maintained explicitly via the entity map (`POST /objects/entity-map`, idempotent bulk).

## ④ Decisions & actions — the execution end

```bash
# Assess (aligned fetch → active-rule evaluation → conclusions + actionable); record_history feeds the flywheel/RLHF
curl -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"dataset":"demo_gas","object_type":"segments","object_id":"GAS.SEG.1"}' \
  "http://127.0.0.1:8000/api/v1/decisions/assess?record_history=true"
# → {"matched_rules": 1, "confidence": 0.9, "conclusions": [...], "actionable": [...],
//   "history_recorded": true}
# confidence uses real down-weighting semantics (v1.11.5): no rule matched → 0.5
# baseline; each unruly rule (uncompilable) −0.1 capped at −0.3; a scenario
# gateway taking the substitute arm multiplies by 0.9. Release-gate thresholds
# are unchanged — low-confidence objects feed the flywheel auto_low_confidence
# via decisions_history.
```

Actions and scenarios are YAML catalogs (version chains, same-hash skips): actions declare effects (update_lifecycle / field writes), idempotency keys, compensation, and audit requirements; scenarios orchestrate multiple steps (steps/gateways/timeouts). Execution runs an eight-step middleware — permission → idempotency → preconditions → **pre-write row-count verification** (a bare identifier physically matching ≠1 rows is refused, blocking cross-partition duplicate-identifier writes) → effect → audit → events:

```bash
curl -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"dataset":"demo_gas","object_type":"segments","object_id":"GAS.SEG.1",
       "reason":"work order #123 closed","assess":{...}}' \
  http://127.0.0.1:8000/api/v1/actions/GAS.SEG.PUBLISH/execute
# → {"status":"executed","audit_id":"..."} or {"status":"already_in_effect"} (idempotent hit)
```

**Scenario runner (promoted in v1.11.5)**: scenarios are now instantiable and executable against a target object — `instantiate` runs the whole flow in the background (XOR gateway arms / AND parallelism / timeout escalation / resume), with the instance row as the source of truth:

```bash
# Instantiate (202 + background run; entry mismatch / missing object → 422)
curl -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"dataset":"demo_gas","object_type":"segments","object_id":"GAS.SEG.1",
       "reason":"automated response"}' \
  http://127.0.0.1:8000/api/v1/actions/scenarios/GAS.LEAK.RESPONSE/instantiate
# → {"instance_id": 1, "status": "running"}
curl -H "X-API-Key: $TOKEN" \
  "http://127.0.0.1:8000/api/v1/actions/scenarios/instances/1"       # detail + step timeline
curl -X POST -H "X-API-Key: $TOKEN" \
  http://127.0.0.1:8000/api/v1/actions/scenarios/instances/1/resume   # resume (EDITOR)
```

A failed step with a declared compensation terminal-states the instance as
`compensated` with a manual compensation backlog (executed one by one from the
console "instances" drawer). The console "Actions & scenarios" page offers a
per-scenario trial-run button and the instance timeline.

**PII classification (v1.11.5)**: four dataset tiers (registry-only), enforced
at the corpus-export boundary — unclassified datasets require an explicit
`?allow_unclassified=true` (audited as `corpus.unclassified`); confidential or
restricted tiers without masking config get a tier-named override prompt:

```bash
curl -X PUT -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"tier":"internal","note":"pilot classification"}' \
  http://127.0.0.1:8000/api/v1/datasets/demo_gas/classification   # audited on change
```

---

## Troubleshooting quick reference

| Symptom | Cause & fix |
|---|---|
| Contract save 422 `dataset field must match scope` | contract scope must equal the dataset name — otherwise the whole contract silently matches nothing |
| assess 422 `no contract` / empty types | object types come from contract table sections — do step ① first; datasets without an identifier column aren't assessable (the wizard says so explicitly) |
| rule changed but assessment unchanged | state machine: only `active` rules evaluate; freshly saved ones are draft |
| action execute 422 `physically matches N rows` | pre-write uniqueness verification tripped (duplicate identifier values) — fix the data or tighten the pattern |
| object query returns the wrong unit | semantic alignment not registered; check `GET /semantic/units` |
| corpus export 422 `unclassified` | W2 #5 classification gate: classify via `PUT /datasets/{name}/classification` first, or pass an explicit `?allow_unclassified=true` (audited) |
| ontology gate always skip | template has no `ontology:` section or GATE_MODE=off; see `arrow_lake_ontology_check_total` on `/metrics` |

Next: [20 HQ Dataset Pipeline](./20-hq-dataset.md) · Deep dive: [ARCHITECTURE](../architecture-design/ARCHITECTURE.md) §v1.11.0–v1.11.2
