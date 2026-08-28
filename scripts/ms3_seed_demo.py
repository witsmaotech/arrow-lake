#!/usr/bin/env python3
"""MS3 演示场景种子脚本(v1.11.2 W5.1/F3.5)。

经 REST(ADMIN)把 testing/ms3_demo.py 的资产种到**运行中的栈**(纯镜像
发版验证用):上传 CSV → 异步摄入 → 契约 → 规则×5(含 unruly)→ 行动
×3 → 场景 → assess 冒烟。幂等:--reset 先删数据集重建。

用法:
    python scripts/ms3_seed_demo.py --api http://127.0.0.1:8000 \
        --token <ADMIN_JWT> [--reset]
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.request
import uuid

from arrow_lake.testing import ms3_demo as demo


def _req(method: str, url: str, token: str, *, json_body=None,
         raw: bytes | None = None, content_type: str | None = None,
         ok=(200, 201)) -> dict:
    import json as _json

    data = None
    headers = {"Authorization": f"Bearer {token}"}
    if raw is not None:
        data = raw
        headers["Content-Type"] = content_type or "application/octet-stream"
    elif json_body is not None:
        data = _json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read()
            assert resp.status in ok, f"{method} {url} -> {resp.status}: {body[:300]}"
            return _json.loads(body) if body else {}
    except urllib.error.HTTPError as e:  # noqa: F821 — urllib.error 运行时可达
        raise SystemExit(f"{method} {url} -> {e.code}: {e.read()[:300]!r}") from e


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--token", required=True, help="ADMIN JWT")
    ap.add_argument("--reset", action="store_true",
                    help="先删除演示数据集再种(重放安全)")
    args = ap.parse_args()
    api = args.api.rstrip("/")
    tok = args.token
    ds = demo.DEMO_DATASET

    if args.reset:
        try:
            _req("DELETE", f"{api}/api/v1/datasets/{ds}", tok, ok=(200, 404))
            print(f"[reset] deleted {ds}")
        except SystemExit:
            pass  # 404 = 不存在,继续

    # ① 上传 CSV → blob key
    boundary = uuid.uuid4().hex
    csv = demo.ALERTS_CSV.encode()
    part = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files"; filename="alerts.csv"\r\n'
            f"Content-Type: text/csv\r\n\r\n").encode() + csv + f"\r\n--{boundary}--\r\n".encode()
    up = _req("POST", f"{api}/api/v1/datasets/{ds}/upload", tok,
              raw=part, content_type=f"multipart/form-data; boundary={boundary}")
    blob_key = up["blobs"][0]["key"]
    print(f"[upload] {blob_key}")

    # ② 异步摄入 → 轮询**数据落库**(⚠️ 不等任务完成:本栈 gravitino
    # fileset 注册无超时悬挂(已知 fileset-schema 路径,新数据集必踩),
    # 但写入在悬挂前已完成且数据集锁已释放——以 schema 可见为准继续。
    # 悬挂任务残留在 running,属既有问题,登记 docs_offline 待修。)
    _req("POST", f"{api}/api/v1/datasets/{ds}/ingest/async", tok,
         json_body={"blob_keys": [blob_key]}, ok=(200, 201, 202))
    landed = False
    for _ in range(90):
        try:
            sch = _req("GET", f"{api}/api/v1/datasets/{ds}/schema", tok)
            if sch.get("fields") or sch.get("columns"):
                landed = True
                break
        except SystemExit:
            pass  # 404 = 尚未建出,继续等
        time.sleep(1)
    if not landed:
        raise SystemExit("ingest data did not land within 90s")
    print(f"[ingest] data landed ({len(sch.get('fields') or sch.get('columns'))} columns)")

    # ③ 契约
    _req("PUT", f"{api}/api/v1/contracts/{ds}", tok,
         json_body={"contract_yaml": demo.CONTRACT_YAML})
    print(f"[contract] {ds} saved")

    # ④ 规则(含 unruly)
    for r in demo.RULES:
        _req("POST", f"{api}/api/v1/ontology/rules", tok, json_body=r, ok=(200, 201))
        _req("POST", f"{api}/api/v1/ontology/rules/{r['rule_id']}/transition"
                     f"?to_status=active", tok)
    print(f"[rules] {len(demo.RULES)} seeded (incl. 1 unruly)")

    # ⑤ 行动 + 场景
    for y in (demo.ACTION_PUBLISH, demo.ACTION_ESCALATE, demo.ACTION_NOTIFY):
        aid = y.splitlines()[1].split(":", 1)[1].strip()
        _req("PUT", f"{api}/api/v1/actions/{aid}", tok,
             json_body={"action_yaml": y})
    _req("PUT", f"{api}/api/v1/actions/scenarios/GAS.LEAK.RESPONSE", tok,
         json_body={"scenario_yaml": demo.SCENARIO_YAML})
    print("[actions] 3 + scenario GAS.LEAK.RESPONSE saved")

    # ⑥ 冒烟:assess D001 应命中黄金集
    a = _req("POST", f"{api}/api/v1/decisions/assess", tok, json_body={
        "dataset": ds, "object_type": "alerts",
        "object_id": "GAS.ALERT.D001"})
    matched = {c["rule_id"] for c in a["conclusions"]}
    assert matched == demo.GOLDEN_EXPECTED["GAS.ALERT.D001"], \
        f"golden mismatch: {matched}"
    assert a["unruly"] == ["DEMO.R.UNRULY"]
    print(f"[smoke] assess D001 matched={sorted(matched)} unruly={a['unruly']}")
    print("SEED OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
