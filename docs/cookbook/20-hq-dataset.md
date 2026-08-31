# 20 · The High-Quality Dataset Pipeline: from Annotation to Release

> Version baseline: v1.11.4 (MS5 five-dimension quality gate + release layer, the Master Plan finale). Every step below is copy-runnable; sample outputs come from the real `demo_annotation_alerts` pilot. Console entry: **HQ Datasets → Production Wizard** shows all six steps with live status.

The HQ-dataset platform turns a plain table into a *trustworthy release artifact*: **contract** (machine-checkable structure) → **annotation** (human ground truth) → **assessment** (five-dimension scoring) → **release** (version lock + datasheet) → **corpus** (four training forms, masked) → **flywheel** (low-confidence requeue closes the loop).

```text
①Contract ─→ ②Annotate ─→ ③Assess ─→ ④Release ─→ ⑤Corpus
                 ↑                                        │
                 └──────── ⑥Flywheel (requeue) ←──────────┘
```

Prereqs: an ADMIN personal token (header `X-API-Key`); Label Studio ships with the default stack (127.0.0.1:8085).

---

## ① Contract — machine-checkable structure

A contract declares object classes, identifier patterns, column rules (required/range/enum) and quality weights. Author it in the modeling workbench (form → YAML), or save YAML directly:

```bash
curl -X PUT "http://127.0.0.1:8000/api/v1/contracts/demo_annotation_alerts" \
  -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" -d '{
  "contract_yaml": "dataset: demo_annotation_alerts\ntables:\n  alerts:\n    object_class: gas_alert_event\n    columns:\n      - {name: text, label: alert text, required: true}\n      - {name: quality_score, label: row score, range: [0, 1]}\nreferences: []\nquality:\n  critical: true\n  drift_kl: 0.1\n"}'
```

```json
{"scope":"demo_annotation_alerts","id":14,"version":1,"created":true,"diff":null}
```

Notes: `dataset` must equal the dataset name (otherwise the contract silently matches nothing); `quality.critical` is a **bool** flag, not a column list; `drift_kl` is the drift threshold (default 0.1).

## ② Annotate — LS human truth → Lance ADL

The annotation line is a bypass (zero hot-path impact): Label Studio is a transient workspace; **the Lance ADL is the single source of truth**.

```bash
# Create a project (labeling config generated from the extraction template)
curl -X POST http://127.0.0.1:8000/api/v1/annotation/projects \
  -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"alerts-l4","dataset":"demo_annotation_alerts","template_name":"project_concept_graph"}'

# Sample-dispatch (mask → pre-annotate → LS import in one shot; strategy budget optional)
curl -X POST http://127.0.0.1:8000/api/v1/annotation/dispatch \
  -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"project":"alerts-l4","total":20,"text_column":"text",
       "generalize_rules":[["1[3-9]\\d{9}","[phone]"]]}'
```

Annotators work in LS (relevance three-way classification + entities/relations); a 30s scheduler recovers automatically (or `POST /annotation/recover`), with a live scoreboard:

```bash
curl -H "X-API-Key: $TOKEN" \
  http://127.0.0.1:8000/api/v1/annotation/projects/alerts-l4/status
```

```json
{"project":"alerts-l4","bound":true,"tasks_total":40,"annotated_rows":10,
 "review":{"approved":10,"arbitration":0,"pending":0},"kappa":0.638,"watermark":42}
```

A second annotator submitting after the recovery window is still captured — the watermark is a **composite cursor** (task-id advance ∪ annotation-count growth on recovered tasks) and LS webhooks trigger immediate re-recovery; single-annotator pilots can set `adjudicate_min_annotators=1`.

## ③ Assess — five dimensions, one-vote vetoes

```bash
curl -X POST -H "X-API-Key: $TOKEN" \
  http://127.0.0.1:8000/api/v1/quality/assess/demo_annotation_alerts
```

```json
{"total_score": 53.89, "star": 1, "admission": "none", "verdict": "veto",
 "dimensions": {"relevance": 75.0, "accuracy": 0.0, "completeness": 100.0,
                "diversity": 90.0, "timeliness": null},
 "vetoes": [{"kind": "accuracy_below_threshold", ...}, ...]}
```

Five dimensions: relevance .20 (annotation loop) / accuracy .35 (ADL-aggregated κ) / completeness .20 (contract + profiler) / diversity .15 (Gini) / timeliness .10 (SLO); weights are overridable in the contract `quality:` node. If relevance is low, run the relevance loop first, then re-assess:

```bash
curl -X POST -H "X-API-Key: $TOKEN" \
  "http://127.0.0.1:8000/api/v1/quality/relevance/demo_annotation_alerts?n=200"
```

## ④ Release — the gate, version lock, datasheet

```bash
# First attempt (the gate returns structured reasons on rejection)
curl -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"changelog":"formal release with contract v1"}' \
  http://127.0.0.1:8000/api/v1/release/demo_annotation_alerts
# → 422 {"detail":{"blocked":["veto:accuracy_below_threshold",...],
//        "decision":"none","hint":"force=true + reason to override (audited)"}}

# Force release (reason required, audited)
curl -X POST ... -d '{"changelog":"...","force":true,"reason":"pilot: kappa=-1 is an archived discordant pair"}' ...
```

```json
{"tag":"v1.4.0","lance_version":1,"status":"active","forced":true,
 "datasheet":{"schema":{"contract_present":true},"labeling":{"annotators":3,"coverage":1.0}, ...}}
```

A release = Lance version lock + semver tag + an auto-generated datasheet YAML; the gate = admission + per-dimension vetoes + **regression rejection** (baseline = latest active) + drift. Datasheet: `GET /release/{ds}/datasheet` (JSON, `yaml` field).

## ⑤ Corpus — four training forms, masked out-of-domain

```bash
curl -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"generalize_rules":[["1[3-9]\\d{9}","[phone]"]]}' \
  "http://127.0.0.1:8000/api/v1/release/demo_annotation_alerts/corpus?form=sft"
```

```json
{"records": 10, "path": "/data/lake/exports/v1.4.0/sft.jsonl",
 "masking": {"applied": true, "rules": 1}}
```

Four forms: `sft` (ADL five-part × source rows) / `pretrain` (KG snapshot triples, definitions masked too) / `rlhf` (decision-history × ADL pairs matched by identifier value) / `golden` (approved human rows; copy into `tests/benchmark/golden/` for regression). **Red line ④: no masking config → 422 always**; `?allow_unmasked=true` is an explicit, audited (`corpus.unmasked`) override.

## ⑥ Flywheel — low-confidence requeue closes the loop

Check "record decision" in the decision console (`POST /decisions/assess?record_history=true`); the history then feeds both RLHF pairing and automatic requeue:

```bash
curl -H "X-API-Key: $TOKEN" \
  http://127.0.0.1:8000/api/v1/decisions/history/demo_gas
# → {"total": 2, "low_confidence": 0, "recent":[{"object_id":"GAS.SEG.1", ...}]}

# Auto-requeue low-confidence objects (object_id resolved against source rows by value)
curl -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"object_rows":[],"auto_low_confidence":true,"confidence_threshold":0.6}' \
  http://127.0.0.1:8000/api/v1/quality/feedback/demo_gas
```

After re-annotation, return to ③ — the score rises, a new version ships, and data quality compounds with use.

---

## Troubleshooting quick reference

| Symptom | Cause & fix |
|---|---|
| Contract save 422 `QualitySpec.critical bool` | `critical` is a boolean flag; column constraints live in `columns` (`required/range/enum`) |
| accuracy=0 + κ=-1 | genuine double-annotation discordance — arbitrate then re-assess; pilots may force (audited) |
| corpus 422 `requires masking config` | red line ④ by design: pass `generalize_rules` or the explicit `allow_unmasked=true` |
| feedback auto always 0 rows | decisions must be recorded with `record_history=true` and fall below the threshold |
| release 422 `no active release ... publish first` | corpus export requires an active release (step ④ first) |

Next: [REST Recipes](./19-rest-recipes.md) · Deep dive: [ARCHITECTURE](../architecture-design/ARCHITECTURE.md) §v1.11.4
