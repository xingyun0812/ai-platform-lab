"""Gateway 集成：Phase Q plan export / approval 路由已挂载。"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from apps.gateway.main import create_app


class TestGatewayPhaseQRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())

    def test_plan_export_route_mounted(self) -> None:
        r = self.client.post(
            "/v1/agent/plan/export",
            headers={
                "X-Tenant-Id": "admin",
                "Authorization": "Bearer sk-tenant-admin-change-me",
            },
            json={
                "plan": {
                    "goal": "gateway route smoke",
                    "steps": [
                        {"id": "s1", "description": "step one", "depends_on": []},
                    ],
                }
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("text/yaml", r.headers.get("content-type", ""))
        self.assertIn("plan_to_workflow", r.text)

    def test_plan_approval_route_mounted(self) -> None:
        r = self.client.get(
            "/v1/agent/plan/approval/nonexistent-id",
            headers={
                "X-Tenant-Id": "admin",
                "Authorization": "Bearer sk-tenant-admin-change-me",
            },
        )
        self.assertEqual(r.status_code, 404)
        body = r.json()
        self.assertEqual(body.get("error", {}).get("code"), "PLAN_APPROVAL_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
