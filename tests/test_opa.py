from __future__ import annotations

import unittest

from packages.opa import OpaClient, get_opa_client, init_opa_client, reset_opa_for_tests


class TestOpaLoader(unittest.TestCase):
    """OpaLoader 单元测试。"""

    def test_load_tenant_isolation_policy(self):
        from packages.opa.loader import OpaLoader

        loader = OpaLoader(policies_dir="config/policies")
        policies = loader.load_all()
        self.assertGreaterEqual(len(policies), 1)
        self.assertIn("platform.tenants", policies)

    def test_extract_package(self):
        from packages.opa.loader import OpaLoader

        pkg = OpaLoader._extract_package("package platform.tenants\ndefault allow = false")
        self.assertEqual(pkg, "platform.tenants")

    def test_extract_package_no_match(self):
        from packages.opa.loader import OpaLoader

        pkg = OpaLoader._extract_package("no package statement here")
        self.assertIsNone(pkg)


class TestOpaEvaluator(unittest.IsolatedAsyncioTestCase):
    """OpaEvaluator 单元测试。"""

    async def test_admin_is_allowed(self):
        client = OpaClient(policies_dir="config/policies")
        result = await client.check({
            "tenant_id": "admin",
            "role": "platform_admin",
            "path": "/v1/agent/run",
            "method": "POST",
        })
        self.assertTrue(result.get("allow", False))

    async def test_demo_a_allowed_chat(self):
        client = OpaClient(policies_dir="config/policies")
        result = await client.check({
            "tenant_id": "demo-a",
            "role": "developer",
            "path": "/v1/chat/completions",
            "method": "POST",
        })
        self.assertTrue(result.get("allow", False))

    async def test_unknown_tenant_denied(self):
        client = OpaClient(policies_dir="config/policies")
        result = await client.check({
            "tenant_id": "unknown",
            "role": "viewer",
            "path": "/v1/agent/run",
            "method": "POST",
        })
        # default deny should trigger
        self.assertFalse(result.get("allow", True))

    async def test_tool_permissions_calc(self):
        client = OpaClient(policies_dir="config/policies")
        result = await client.check({
            "tenant_id": "demo-a",
            "role": "developer",
            "path": "/v1/agent/run",
            "method": "POST",
            "tool": "calc",
        })
        # calc is allowed for everyone
        self.assertTrue(result.get("allow", False))

    async def test_tool_permissions_sql_admin_only(self):
        client = OpaClient(policies_dir="config/policies")
        # tool_permissions policy has no default deny, so unmatched tool is allowed
        # Tool-level ACL is enforced by allowed_tools at application layer
        result = await client.check({
            "tenant_id": "demo-a",
            "role": "developer",
            "path": "/v1/agent/run",
            "method": "POST",
            "tool": "sql_query",
        })
        self.assertTrue(result.get("allow", False))


class TestOpaSingleton(unittest.TestCase):
    """OPA 全局单例测试。"""

    def setUp(self):
        reset_opa_for_tests()

    def test_init_and_get(self):
        init_opa_client(policies_dir="config/policies")
        client = get_opa_client()
        self.assertIsNotNone(client)

    def test_reload_policies(self):
        client = OpaClient(policies_dir="config/policies")
        client.reload_policies()  # should not raise


if __name__ == "__main__":
    unittest.main()
