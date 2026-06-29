"""tests/test_strategy_patch_routes.py — #146 7b strategy patch REST 集成测。"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from apps.gateway.main import create_app
from packages.agent.self_evolve import StrategyPatch, reset_strategy_patch_store_for_tests


def _admin_headers() -> dict[str, str]:
    return {
        "X-Tenant-Id": "admin",
        "Authorization": "Bearer sk-tenant-admin-change-me",
    }


class TestStrategyPatchRoutes(unittest.TestCase):
    def setUp(self) -> None:
        reset_strategy_patch_store_for_tests()
        self.client = TestClient(create_app())
        store = __import__(
            "packages.agent.self_evolve",
            fromlist=["get_strategy_patch_store"],
        ).get_strategy_patch_store()
        store.add(
            StrategyPatch(
                patch_id="patch-list-1",
                tenant_id="admin",
                lessons="lessons",
                proposed_change={"field": "plan_prompt", "old": "a", "new": "b"},
                status="pending",
            )
        )

    def tearDown(self) -> None:
        reset_strategy_patch_store_for_tests()

    def test_list_strategy_patches(self) -> None:
        resp = self.client.get(
            "/internal/agent/strategy-patches",
            headers=_admin_headers(),
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertGreaterEqual(body["count"], 1)
        self.assertTrue(any(i["patch_id"] == "patch-list-1" for i in body["items"]))

    def test_list_filter_by_status(self) -> None:
        resp = self.client.get(
            "/internal/agent/strategy-patches",
            params={"status": "pending"},
            headers=_admin_headers(),
        )
        self.assertEqual(resp.status_code, 200)
        for item in resp.json()["items"]:
            self.assertEqual(item["status"], "pending")

    def test_approve_strategy_patch(self) -> None:
        resp = self.client.post(
            "/internal/agent/strategy-patches/patch-list-1/approve",
            headers=_admin_headers(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "approved")
        self.assertEqual(resp.json()["decided_by"], "admin")

    def test_reject_unknown_returns_404(self) -> None:
        resp = self.client.post(
            "/internal/agent/strategy-patches/missing-id/reject",
            headers=_admin_headers(),
        )
        self.assertEqual(resp.status_code, 404)

    def test_unauthorized_without_token(self) -> None:
        resp = self.client.get("/internal/agent/strategy-patches")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
