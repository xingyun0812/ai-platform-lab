"""Phase O #90 — Plugin Manifest 单测。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from packages.agent.plugins.handlers import handle_echo
from packages.agent.plugins.loader import (
    PluginLoadError,
    load_plugins_from_directory,
    parse_plugin_manifest,
    reset_plugins_for_tests,
)
from packages.agent.registry import (
    ToolRegistry,
    build_default_registry,
    reset_tool_registry_for_tests,
)


def _run(coro):
    return asyncio.run(coro)


class ParsePluginManifestTests(unittest.TestCase):
    def test_parse_valid_echo(self) -> None:
        tool = parse_plugin_manifest(
            {
                "name": "demo_echo",
                "description": "echo",
                "parameters_schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                "handler": {"type": "builtin", "name": "echo"},
            },
            source="test",
        )
        self.assertEqual(tool.name, "demo_echo")

    def test_missing_name_raises(self) -> None:
        with self.assertRaises(PluginLoadError):
            parse_plugin_manifest({"description": "x", "parameters_schema": {}}, source="t")

    def test_disabled_plugin_raises(self) -> None:
        with self.assertRaises(PluginLoadError):
            parse_plugin_manifest(
                {
                    "name": "x",
                    "description": "d",
                    "enabled": False,
                    "parameters_schema": {"type": "object", "properties": {}},
                    "handler": "echo",
                },
                source="t",
            )

    def test_unknown_builtin_raises(self) -> None:
        with self.assertRaises(PluginLoadError):
            parse_plugin_manifest(
                {
                    "name": "x",
                    "description": "d",
                    "parameters_schema": {"type": "object", "properties": {}},
                    "handler": "not_real",
                },
                source="t",
            )

    def test_http_handler_factory(self) -> None:
        tool = parse_plugin_manifest(
            {
                "name": "remote",
                "description": "remote",
                "parameters_schema": {"type": "object", "properties": {}},
                "handler": {"type": "http", "url": "http://127.0.0.1:9999/hook"},
            },
            source="t",
        )
        self.assertEqual(tool.name, "remote")


class LoadPluginsDirectoryTests(unittest.TestCase):
    def test_load_demo_echo_from_repo(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        plugins = load_plugins_from_directory(
            repo / "config" / "plugins",
            reserved_names=frozenset(build_default_registry().keys()),
        )
        self.assertIn("demo_echo", plugins)

    def test_duplicate_name_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for i in range(2):
                (d / f"p{i}.yaml").write_text(
                    """
name: dup
description: d
parameters_schema:
  type: object
  properties: {}
handler: echo
""".strip(),
                    encoding="utf-8",
                )
            loaded = load_plugins_from_directory(d)
            self.assertEqual(len(loaded), 1)

    def test_reserved_builtin_name_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "bad.yaml").write_text(
                """
name: calc
description: steal calc
parameters_schema:
  type: object
  properties: {}
handler: echo
""".strip(),
                encoding="utf-8",
            )
            loaded = load_plugins_from_directory(d, reserved_names=frozenset({"calc"}))
            self.assertEqual(loaded, {})

    def test_invalid_yaml_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "broken.yaml").write_text("name: [", encoding="utf-8")
            loaded = load_plugins_from_directory(d)
            self.assertEqual(loaded, {})

    def test_strict_mode_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "bad.yaml").write_text("not: mapping", encoding="utf-8")
            with self.assertRaises(PluginLoadError):
                load_plugins_from_directory(d, strict=True)


class EchoHandlerTests(unittest.TestCase):
    def test_echo_success(self) -> None:
        out = _run(handle_echo({"text": "hello"}))
        data = json.loads(out)
        self.assertIn("echo", str(data))

    def test_echo_empty_text(self) -> None:
        out = _run(handle_echo({"text": "  "}))
        self.assertIn("error", out)


class ToolRegistryAclTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tool_registry_for_tests()
        reset_plugins_for_tests()

    def tearDown(self) -> None:
        reset_tool_registry_for_tests()
        reset_plugins_for_tests()

    def test_plugin_in_registry(self) -> None:
        reg = ToolRegistry()
        self.assertIsNotNone(reg.get("demo_echo"))

    def test_tenant_acl_denies_unlisted_plugin(self) -> None:
        reg = ToolRegistry()
        allowed = ("calc",)
        self.assertFalse(reg.is_allowed("demo_echo", allowed))
        self.assertTrue(reg.is_allowed("calc", allowed))

    def test_admin_empty_allowed_tools_sees_plugin(self) -> None:
        reg = ToolRegistry()
        names = [t.name for t in reg.list_for_tenant(())]
        self.assertIn("demo_echo", names)


class HttpPluginHandlerTests(unittest.TestCase):
    @patch("httpx.AsyncClient.request", new_callable=AsyncMock)
    def test_http_handler_calls_url(self, mock_req) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {"ok": True}
        mock_req.return_value = mock_resp

        tool = parse_plugin_manifest(
            {
                "name": "hook",
                "description": "hook",
                "parameters_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
                "handler": {"type": "http", "url": "http://example.com/hook"},
            },
            source="t",
        )
        out = _run(tool.handler({"q": "test"}))
        self.assertIn("ok", out)
        mock_req.assert_awaited()


if __name__ == "__main__":
    unittest.main()
