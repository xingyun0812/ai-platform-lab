#!/usr/bin/env python3
"""tests/test_rag_query_version.py — Issue #152 PR-2 版本解析边界测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.platform import configure, reset_platform_for_tests
from packages.platform.testing import InMemoryPlatformPort
from packages.rag.query_version import resolve_query_version


class TestResolveQueryVersion(unittest.TestCase):
    def setUp(self) -> None:
        reset_platform_for_tests()
        configure(InMemoryPlatformPort())

    def tearDown(self) -> None:
        reset_platform_for_tests()

    def test_explicit_version_pinned(self) -> None:
        ver, label, bucket = resolve_query_version(
            "kb1",
            3,
            tenant_id="t1",
            query="hello",
        )
        self.assertEqual(ver, 3)
        self.assertEqual(label, "pinned")

    @patch("packages.rag.query_version.list_kb_versions", return_value=[1, 2])
    @patch("packages.rag.query_version.kb_routing_rules", return_value={})
    def test_no_routing_uses_latest(self, _rules, _versions) -> None:
        ver, label, _ = resolve_query_version(
            "kb1",
            None,
            tenant_id="t1",
            query="hello",
        )
        self.assertEqual(ver, 2)
        self.assertEqual(label, "stable")


if __name__ == "__main__":
    unittest.main()
