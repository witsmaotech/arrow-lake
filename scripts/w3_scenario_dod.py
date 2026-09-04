#!/usr/bin/env python3
"""W3 场景执行引擎 DoD 验证脚本(v1.11.5)。

在**运行中的栈**上验证 docs_offline/v1115-w3-scenario-runner-design.md
§五.4 DoD:三步场景自动跑通 + XOR 双臂 + 超时升级 + 补偿路径各一次。

前置:scripts/ms3_seed_demo.py 已种 demo_ms3_alerts + GAS.LEAK.RESPONSE。
本脚本追加注册超时/补偿演示资产(同 hash 幂等),对象用后不复位——
D001/D002 状态会被翻转,重跑前先 seed --reset 或手动复位 state=pending。

用法:
    python scripts/w3_scenario_dod.py --api http://127.0.0.1:8000 \
        --token <ADMIN 个人 token(al_ 前缀)或 JWT>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

ACT_TIMEOUTLOG = """
action_id: DEMO.ALERT.TIMEOUTLOG
title: 超时升级登记
target: {dataset: demo_ms3_alerts, object_class: 告警事件}
effect: {type: none}
"""

ACT_BADSTEP = """
action_id: DEMO.ALERT.BADSTEP
title: 会撞词表守卫(演示补偿)
target: {dataset: demo_ms3_alerts, object_class: 告警事件}
effect: {type: update_lifecycle, to_state: nonexistent_state}
compensation: {action: DEMO.ALERT.WITHDRAW, policy: manual}
"""

ACT_WITHDRAW = """
action_id: DEMO.ALERT.WITHDRAW
title: 撤回(人工补偿)
target: {dataset: demo_ms3_alerts, object_class: 告警事件}
effect: {type: update_lifecycle, to_state: pending}
"""

SCN_TIMEOUT = """
scenario_id: DEMO.SCN.TIMEOUT
title: 超时升级演示
steps:
  - {id: publish, action: GAS.ALERT.PUBLISH}
  - {id: escalate, action: DEMO.ALERT.TIMEOUTLOG}
timeout: PT0S
on_timeout: escalate
"""

SCN_COMPENSATE = """
scenario_id: DEMO.SCN.COMPENSATE
title: 补偿路径演示
steps:
  - {id: bad, action: DEMO.ALERT.BADSTEP}
"""


def _req(method: str, url: str, token: str, *, body=None, ok=(200, 201, 202)) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-API-Key": token, "Authorization": f"Bearer {token}"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            assert resp.status in ok, f"{method} {url} -> {resp.status}"
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:  # noqa: F821
        raise SystemExit(f"{method} {url} -> {e.code}: {e.read()[:300]!r}") from e


def _await_terminal(api: str, tok: str, iid: int, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = _req("GET", f"{api}/api/v1/actions/scenarios/instances/{iid}", tok)
        if d["instance"]["status"] != "running":
            return d
        time.sleep(0.3)
    raise SystemExit(f"instance {iid} still running after {timeout}s")


def _register(api: str, tok: str) -> None:
    for yml, path in (
        (ACT_TIMEOUTLOG, "/actions/DEMO.ALERT.TIMEOUTLOG"),
        (ACT_BADSTEP, "/actions/DEMO.ALERT.BADSTEP"),
        (ACT_WITHDRAW, "/actions/DEMO.ALERT.WITHDRAW"),
        (SCN_TIMEOUT, "/actions/scenarios/DEMO.SCN.TIMEOUT"),
        (SCN_COMPENSATE, "/actions/scenarios/DEMO.SCN.COMPENSATE"),
    ):
        field = "scenario_yaml" if "/scenarios/" in path else "action_yaml"
        _req("PUT", f"{api}/api/v1{path}", tok, body={field: yml})
    print("[0] dod assets registered (idempotent)")


def _instantiate(api: str, tok: str, scn: str, obj: str) -> int:
    r = _req(
        "POST", f"{api}/api/v1/actions/scenarios/{scn}/instantiate", tok,
        body={
            "dataset": "demo_ms3_alerts", "object_type": "alerts",
            "object_id": obj, "reason": "W3 DoD 验证",
        },
    )
    return r["instance_id"]


def _runs(detail: dict) -> dict[str, str]:
    return {s["step_id"]: s["status"] for s in detail["step_runs"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--token", required=True)
    args = ap.parse_args()
    api, tok = args.api.rstrip("/"), args.token

    _register(api, tok)

    # ── ① XOR 低命中臂:D002(matched=1 < 2)→ escalate_manual ──────────
    d = _await_terminal(api, tok, _instantiate(api, tok, "GAS.LEAK.RESPONSE", "GAS.ALERT.D002"))
    assert d["instance"]["status"] == "completed", d["instance"]
    runs = _runs(d)
    assert runs == {
        "assess": "succeeded", "escalate_manual": "succeeded",
        "publish": "skipped", "notify_ops": "skipped",
    }, runs
    print("[1] XOR else arm (D002 low-confidence → escalate):", runs)

    # ── ② 超时升级:PT0S → publish 标 timeout,on_timeout 升级步执行 ────
    d = _await_terminal(api, tok, _instantiate(api, tok, "DEMO.SCN.TIMEOUT", "GAS.ALERT.D001"))
    assert d["instance"]["status"] == "timeout", d["instance"]["status"]
    runs = _runs(d)
    assert runs == {"publish": "timeout", "escalate": "succeeded"}, runs
    print("[2] timeout escalation:", runs)

    # ── ③ 补偿:词表守卫 422 → compensated + 人工补偿项 → 逐条核销 ──────
    d = _await_terminal(api, tok, _instantiate(api, tok, "DEMO.SCN.COMPENSATE", "GAS.ALERT.D001"))
    inst = d["instance"]
    assert inst["status"] == "compensated", inst["status"]
    assert inst["pending_compensation"] == ["DEMO.ALERT.WITHDRAW"], inst
    bad = next(s for s in d["step_runs"] if s["step_id"] == "bad")
    assert bad["output"]["pending_compensation"] == ["DEMO.ALERT.WITHDRAW"]
    w = _req(
        "POST", f"{api}/api/v1/actions/DEMO.ALERT.WITHDRAW/execute", tok,
        body={
            "dataset": "demo_ms3_alerts", "object_type": "alerts",
            "object_id": "GAS.ALERT.D001", "reason": "补偿核销(W3 DoD)",
            "scenario_id": "DEMO.SCN.COMPENSATE", "step_id": "bad",
        },
    )
    assert w["status"] == "executed", w
    print("[3] compensation: compensated + manual withdraw executed")

    print("W3 DoD ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
