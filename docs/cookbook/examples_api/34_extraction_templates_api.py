#!/usr/bin/env python3
"""API-34 — Extraction Template Lifecycle (v1.10.0 ⚑ flagship)

Business scenario: manage the hyper-extract knowledge-extraction template that
drives KG building — author/validate a YAML, bind it to a dataset, and rebuild
the graph without touching the Docker image.

Capabilities (v1.10.0):
  * List / get / create / update / delete templates (user volume).
  * Validate a draft YAML without saving (field-level errors).
  * Bind a template to a dataset so subsequent ``/kg/build`` (no ``template``
    body) auto-resolves the binding.
  * Optional: LLM-assisted authoring (``/generate``) + end-to-end quality
    harness (``/quality/doc`` → ``/quality/build`` → ``/quality/{temp_ds}``).

NOTE: All endpoints live under ``/api/v1/admin/extraction-templates`` and
require the ADMIN role (the dev key ``dev-api-key-for-local-testing-only`` is
an ADMIN key).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = os.environ.get("ARROW_LAKE_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("ARROW_LAKE_API_KEY", "dev-api-key-for-local-testing-only")
ADMIN = "/api/v1/admin/extraction-templates"

# A minimal but valid graph template (security domain). Mirrors the structure
# the validator expects: output.entities/relations + guideline + identifiers.
SECURITY_YAML = """\
language: [zh, en]
name: security_concept_graph
type: graph
tags: [security, concept, knowledge]
description: {zh: "安全领域概念图谱", en: "Security concept graph"}
output:
  description: {zh: "资产/威胁/控制及其关联", en: "assets, threats, controls"}
  entities:
    description: {zh: "安全实体", en: "security entities"}
    fields:
      - {name: name, type: str, description: {zh: "规范术语", en: "canonical name"}, required: true}
      - {name: type, type: str, description: {zh: "资产/威胁/控制之一", en: "asset/threat/control"}, required: true}
      - {name: definition, type: str, description: {zh: "一句话定义", en: "definition"}, required: true}
  relations:
    description: {zh: "实体关系", en: "entity relations"}
    fields:
      - {name: source, type: str, required: true}
      - {name: target, type: str, required: true}
      - {name: type, type: str, description: {zh: "威胁利用/控制缓解/相关", en: "exploits/mitigates/related_to"}, required: true}
guideline:
  target: {zh: "你是安全知识图谱专家", en: "You are a security KG expert"}
  rules_for_entities:
    zh: ["type 必须是 资产/威胁/控制 之一", "name 用规范术语"]
    en: ["type must be one of asset/threat/control", "use canonical names"]
  rules_for_relations:
    zh: ["type 严格按枚举", "仅抽明确表述的关系"]
    en: ["type must follow enum", "only explicit relations"]
identifiers:
  entity_id: name
  relation_id: '{source}|{type}|{target}'
  relation_members: {source: source, target: target}
display:
  entity_label: '{name} ({type})'
  relation_label: '{type}'
"""

DS_NAME = "tpl-demo-ds"
TPL_NAME = "security_concept_graph"


def main() -> None:
    print("=" * 64)
    print("API-34  Extraction Template Lifecycle (v1.10.0 flagship)")
    print("=" * 64)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    # --- Step 1: list templates (system + project + user) ---
    print("\n# --- Step 1: list templates ---")
    resp = c.get("/list-templates") if False else c._request("GET", ADMIN + "?selectable=true")
    print(json.dumps(resp, ensure_ascii=False)[:400])
    if resp.get("success"):
        print(f"  [INFO] gallery has {resp.get('count')} templates")

    # --- Step 2: validate a draft YAML (no save) ---
    print("\n# --- Step 2: validate draft YAML (POST /validate) ---")
    resp = c._request("POST", f"{ADMIN}/validate", {"yaml": SECURITY_YAML})
    valid = resp.get("data", {}).get("valid", False)
    print(f"  -> valid={valid}")
    if not valid:
        # show field-level errors so an author knows where to fix
        print(f"  errors: {resp.get('data', {}).get('errors')}")

    # --- Step 3: create / upsert the user template ---
    print(f"\n# --- Step 3: create user template '{TPL_NAME}' (POST '') ---")
    body = {"name": TPL_NAME, "yaml": SECURITY_YAML, "doc_type": "security",
            "description": "security domain concept graph (cookbook)"}
    resp = c._request("POST", ADMIN, body)
    print(f"  -> success={resp.get('success')} status={resp.get('status')}")
    if not resp.get("success") and resp.get("status") != 409:
        # 409 = name shadows a system template; otherwise proceed
        print(f"  detail: {resp.get('detail') or resp.get('error')}")

    # --- Step 4: bind it to a dataset (PUT /bindings/{dataset}) ---
    print(f"\n# --- Step 4: bind template to dataset '{DS_NAME}' ---")
    resp = c._request("PUT", f"{ADMIN}/bindings/{DS_NAME}", {"template": TPL_NAME})
    print(f"  -> success={resp.get('success')} detail={resp.get('detail') or resp.get('data')}")
    # read it back
    resp = c._request("GET", f"{ADMIN}/bindings/{DS_NAME}")
    print(f"  bound template = {resp.get('data', {}).get('template')}")

    # --- Step 5: kg_build auto-resolves the binding (no template body) ---
    print("\n# --- Step 5: kg/build picks up the binding automatically ---")
    print("  (POST /api/v1/kg/build with {dataset} only; explicit template wins,")
    print("   else the dataset binding from system_db is used — see KGBuildRequest.model)")
    resp = c._request("POST", "/api/v1/kg/build", {"dataset": DS_NAME})
    task_id = resp.get("task_id")
    print(f"  -> task_id={task_id} status={resp.get('status')}")
    if task_id:
        for _ in range(6):  # quick status peek (build may take minutes)
            st = c._request("GET", f"/api/v1/kg/build/{task_id}/status")
            print(f"     ... {st.get('status')} chunks={st.get('processed_chunks')}/{st.get('total_chunks')}")
            if st.get("status") in ("completed", "done", "success", "failed", "error"):
                break
            time.sleep(3)

    # --- Step 6: dry-run the template against a sample (POST /dry-run) ---
    print("\n# --- Step 6: dry-run template on sample text (POST /dry-run) ---")
    sample = "Web 服务器(资产)存在未修复的远程命令执行漏洞。攻击者利用该漏洞(威胁)可获取主机控制权。" \
             "部署的 WAF(控制)缓解了该威胁。"
    resp = c._request("POST", f"{ADMIN}/dry-run",
                      {"template_name": TPL_NAME, "sample_text": sample})
    data = resp.get("data", {})
    print(f"  -> entities={data.get('entity_count')} relations={data.get('relation_count')} "
          f"elapsed_ms={data.get('elapsed_ms')}")
    if resp.get("status") == 503:
        print("  [INFO] KG extractor unavailable (hugegraph/LLM not configured) — dry-run needs LLM")

    # --- Step 7 (optional): AI authoring + quality harness ---
    print("\n# --- Step 7 (optional): LLM generate + quality validation ---")
    print("  These need an LLM (he_extract_llm). Skipped unless LLM is configured.")
    gen = c._request("POST", f"{ADMIN}/generate",
                     {"prompt": "a security concept graph for cloud assets", "doc_type": "security"})
    if gen.get("success"):
        print(f"  /generate -> valid={gen.get('data', {}).get('valid')} "
              f"healed={gen.get('data', {}).get('healed')}")
    else:
        print(f"  /generate unavailable: {gen.get('detail') or gen.get('error')}")

    # quality harness: doc -> build -> cleanup
    qdoc = c._request("POST", f"{ADMIN}/{TPL_NAME}/quality/doc", {"scenario_hint": "云安全场景"})
    if qdoc.get("success"):
        document = qdoc["data"]["document"]
        print(f"  /quality/doc -> {len(document)} chars of scenario text")
        qbuild = c._request("POST", f"{ADMIN}/{TPL_NAME}/quality/build", {"document": document})
        if qbuild.get("success"):
            temp_ds = qbuild["data"]["temp_dataset"]
            print(f"  /quality/build -> temp_dataset={temp_ds} kg_task={qbuild['data']['kg_task_id']}")
            # cleanup the temp dataset + its kg graph
            cleanup = c._request("DELETE", f"{ADMIN}/quality/{temp_ds}")
            print(f"  cleanup temp dataset -> success={cleanup.get('success')}")

    # --- Step 8: cleanup binding + template ---
    print("\n# --- Step 8: cleanup (clear binding, delete user template) ---")
    c._request("DELETE", f"{ADMIN}/bindings/{DS_NAME}")
    c._request("DELETE", f"{ADMIN}/{TPL_NAME}")
    c._pass("template lifecycle complete")

    print("\n" + "=" * 64)
    print("API-34  Extraction Template Lifecycle — DONE")
    print("=" * 64)


if __name__ == "__main__":
    main()
