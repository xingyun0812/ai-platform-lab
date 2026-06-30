"""TelemetryRegistry 单测 — Issue #186 / 架构 §10"""

from __future__ import annotations

import unittest

from packages.observability.telemetry_registry import (
    bootstrap_default_collectors,
    prometheus_text,
    register_prometheus_collector,
    reset_telemetry_registry_for_tests,
)


class TestTelemetryRegistry(unittest.TestCase):
    def setUp(self) -> None:
        reset_telemetry_registry_for_tests()

    def tearDown(self) -> None:
        reset_telemetry_registry_for_tests()

    def test_register_collector_aggregated(self) -> None:
        register_prometheus_collector("demo", lambda: "# custom_metric 1\n")
        text = prometheus_text(include_core=False)
        self.assertIn("custom_metric", text)

    def test_bootstrap_includes_agent_perf_metric(self) -> None:
        bootstrap_default_collectors()
        text = prometheus_text()
        self.assertIn("http_requests_total", text)
        self.assertIn("agent_self_evolve_experiences_total", text)


class TestGatewayMetricsEndpoint(unittest.TestCase):
    def test_metrics_endpoint_contains_key_names(self) -> None:
        from tests.gateway_client import LifespanTestClient

        with LifespanTestClient() as client:
            resp = client.get("/metrics")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.text
        self.assertIn("http_requests_total", body)
        self.assertIn("agent_self_evolve_experiences_total", body)


if __name__ == "__main__":
    unittest.main()
