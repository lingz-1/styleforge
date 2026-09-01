"""Static contracts for the local Prometheus and Grafana deployment."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
MONITORING_ROOT = ROOT / "deploy" / "monitoring"


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def test_prometheus_scrapes_only_the_styleforge_metrics_endpoint() -> None:
    config = load_yaml(MONITORING_ROOT / "prometheus" / "prometheus.yml")

    jobs = config["scrape_configs"]
    assert len(jobs) == 1
    assert jobs[0]["job_name"] == "styleforge-api"
    assert jobs[0]["metrics_path"] == "/metrics"
    assert jobs[0]["file_sd_configs"][0]["files"] == [
        "/etc/prometheus/targets.json"
    ]
    assert config["rule_files"] == ["/etc/prometheus/alerts.yml"]


def test_alert_rules_cover_availability_errors_latency_and_degradation() -> None:
    config = load_yaml(MONITORING_ROOT / "prometheus" / "alerts.yml")
    rules = [rule for group in config["groups"] for rule in group["rules"]]
    alerts = {rule["alert"]: rule for rule in rules}

    assert len(alerts) == len(rules)
    assert {
        "StyleForgeApiDown",
        "StyleForgeHighHttpFailureRatio",
        "StyleForgeHighAverageHttpLatency",
        "StyleForgeDatabaseOperationFailure",
        "StyleForgeRepeatedLlmFailures",
        "StyleForgeRetryStorm",
        "StyleForgeDegradedExecution",
    } == set(alerts)
    assert all(rule.get("for") for rule in rules)
    assert all(rule["labels"]["severity"] in {"warning", "critical"} for rule in rules)
    assert "database_session" in alerts["StyleForgeDatabaseOperationFailure"]["expr"]
    assert "llm_call" in alerts["StyleForgeRepeatedLlmFailures"]["expr"]


def test_compose_is_loopback_only_pinned_and_persists_inside_workspace() -> None:
    config = load_yaml(MONITORING_ROOT / "compose.yml")
    services = config["services"]

    assert services["prometheus"]["image"] == "prom/prometheus:v3.5.5"
    assert services["grafana"]["image"] == "grafana/grafana:13.1.0"
    assert services["prometheus"]["ports"] == [
        "127.0.0.1:${STYLEFORGE_PROMETHEUS_PORT:-9090}:9090"
    ]
    assert services["grafana"]["ports"] == [
        "127.0.0.1:${STYLEFORGE_GRAFANA_PORT:-3300}:3000"
    ]
    volumes = services["prometheus"]["volumes"] + services["grafana"]["volumes"]
    assert any("../../artifacts/monitoring/prometheus" in volume for volume in volumes)
    assert any("../../artifacts/monitoring/grafana" in volume for volume in volumes)
    assert any("prometheus-targets.json" in volume for volume in volumes)
    assert all("privileged" not in service for service in services.values())


def test_grafana_dashboard_uses_provisioned_prometheus_and_core_metrics() -> None:
    datasource = load_yaml(
        MONITORING_ROOT
        / "grafana"
        / "provisioning"
        / "datasources"
        / "prometheus.yml"
    )
    dashboard_path = (
        MONITORING_ROOT
        / "grafana"
        / "dashboards"
        / "styleforge-overview.json"
    )
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

    assert datasource["datasources"][0]["uid"] == "styleforge-prometheus"
    assert dashboard["uid"] == "styleforge-system-overview"
    assert dashboard["refresh"] == "15s"
    expressions = "\n".join(
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    )
    assert "styleforge_http_requests_total" in expressions
    assert "styleforge_operation_calls_total" in expressions
    assert "styleforge_operation_degraded_total" in expressions
    assert "styleforge_errors_by_code_total" in expressions
