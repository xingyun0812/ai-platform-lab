#!/usr/bin/env python3
"""tests/test_gateway_create_app.py — Issue #156 Gateway create_app 边界 HTTP 测试。"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from apps.gateway.main import create_app


class TestGatewayCreateApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())

    def test_healthz(self) -> None:
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body.get("status"), "ok")
        self.assertIn("version", body)

    def test_chat_route_mounted_unauthorized_without_headers(self) -> None:
        resp = self.client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )
        self.assertIn(resp.status_code, (401, 403))

    def test_long_run_route_mounted(self) -> None:
        resp = self.client.post(
            "/v1/agent/long-run",
            json={
                "plan": {
                    "goal": "smoke",
                    "steps": [{"id": "s1", "description": "step", "depends_on": []}],
                },
                "session_id": "sess-smoke",
            },
            headers={
                "X-Tenant-Id": "admin",
                "Authorization": "Bearer sk-tenant-admin-change-me",
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)

    def test_harness_profiles_route_mounted(self) -> None:
        resp = self.client.get(
            "/internal/harness/profiles",
            headers={
                "X-Tenant-Id": "admin",
                "Authorization": "Bearer sk-tenant-admin-change-me",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)


if __name__ == "__main__":
    unittest.main()
