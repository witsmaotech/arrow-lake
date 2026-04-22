"""Unit tests for Prometheus alert rules — validates PromQL expressions."""

from __future__ import annotations

import re

import yaml


def _load_rules() -> list[dict]:
    """Load alert rules from the Helm template (parsed as YAML)."""
    template_path = "deploy/helm/arrow-lake/templates/prometheusrule.yaml"
    with open(template_path) as f:
        content = f.read()
    # Strip Helm templating
    content = re.sub(r"\{\{-.*?\}\}", "", content, flags=re.DOTALL)
    content = re.sub(r"\{\{.*?\}\}", "", content)
    parsed = yaml.safe_load(content)
    groups = parsed.get("spec", {}).get("groups", [])
    rules = []
    for group in groups:
        rules.extend(group.get("rules", []))
    return rules


def test_alert_rules_parse() -> None:
    """All alert rules should be valid YAML with required fields."""
    rules = _load_rules()
    assert len(rules) >= 10, f"Expected at least 10 alert rules, got {len(rules)}"

    for rule in rules:
        assert "alert" in rule, f"Missing 'alert' field in rule: {rule}"
        assert "expr" in rule, f"Missing 'expr' in alert: {rule['alert']}"
        assert "for" in rule, f"Missing 'for' in alert: {rule['alert']}"
        assert rule["expr"].strip(), f"Empty expr in alert: {rule['alert']}"
        assert rule["for"].strip(), f"Empty 'for' in alert: {rule['alert']}"


def test_severity_labels_present() -> None:
    """Every alert should have a severity label."""
    rules = _load_rules()
    for rule in rules:
        severity = rule.get("labels", {}).get("severity")
        assert severity in ("warning", "critical"), (
            f"Alert {rule['alert']} has invalid severity: {severity}"
        )


def test_expected_alerts_exist() -> None:
    """Verify all planned alerts are defined."""
    rules = _load_rules()
    alert_names = {r["alert"] for r in rules}

    expected = {
        "ArrowLakeIngestionErrorRate",
        "ArrowLakeIngestionErrorRateCritical",
        "ArrowLakeQueryLatencyHigh",
        "ArrowLakeQueryLatencyCritical",
        "ArrowLakeSystemDegraded",
        "ArrowLakeHTTPErrorRateHigh",
        "ArrowLakeHTTPErrorRateCritical",
        "ArrowLakeAuthFailureRateHigh",
        "ArrowLakeIngestionStalled",
        "ArrowLakeRateLimitHigh",
        "ArrowLakeMemoryHigh",
    }

    missing = expected - alert_names
    assert not missing, f"Missing expected alerts: {missing}"


def test_http_error_alert_uses_5xx_filter() -> None:
    """HTTP error rate alerts should filter on 5xx status codes."""
    rules = _load_rules()
    http_alerts = [
        r for r in rules if "HTTPErrorRate" in r["alert"]
    ]
    assert len(http_alerts) >= 2

    for alert in http_alerts:
        expr = alert["expr"]
        assert "5.." in expr or "status_code" in expr, (
            f"HTTP error alert {alert['alert']} missing 5xx filter"
        )


def test_auth_alert_uses_auth_metric() -> None:
    """Auth failure rate alert should use the auth_requests_total metric."""
    rules = _load_rules()
    auth_alerts = [
        r for r in rules if "AuthFailure" in r["alert"]
    ]
    assert len(auth_alerts) == 1
    assert "arrow_lake_auth_requests_total" in auth_alerts[0]["expr"]


def test_rate_limit_alert_uses_rate_limit_metric() -> None:
    """Rate limit alert should use the rate_limit_rejected_total metric."""
    rules = _load_rules()
    rl_alerts = [
        r for r in rules if "RateLimitHigh" in r["alert"]
    ]
    assert len(rl_alerts) == 1
    assert "arrow_lake_rate_limit_rejected_total" in rl_alerts[0]["expr"]


def test_critical_alerts_have_shorter_for() -> None:
    """Critical alerts should fire faster than warning equivalents."""
    rules = _load_rules()

    def _parse_duration(s: str) -> int:
        m = re.match(r"(\d+)([mhs])", s.strip())
        if not m:
            return 0
        val = int(m.group(1))
        unit = m.group(2)
        return val * {"m": 60, "h": 3600, "s": 1}[unit]

    # Ingestion: warning=5m, critical=2m
    warn = next(r for r in rules if r["alert"] == "ArrowLakeIngestionErrorRate")
    crit = next(r for r in rules if r["alert"] == "ArrowLakeIngestionErrorRateCritical")
    assert _parse_duration(warn["for"]) > _parse_duration(crit["for"])

    # HTTP: warning=5m, critical=2m
    warn2 = next(r for r in rules if r["alert"] == "ArrowLakeHTTPErrorRateHigh")
    crit2 = next(r for r in rules if r["alert"] == "ArrowLakeHTTPErrorRateCritical")
    assert _parse_duration(warn2["for"]) > _parse_duration(crit2["for"])
