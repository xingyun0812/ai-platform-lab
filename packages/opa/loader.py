from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("ai_platform.opa.loader")


@dataclass
class OpaPolicy:
    """加载后的 Rego 策略。"""

    package: str
    content: str
    path: str


class OpaLoader:
    """Rego 策略文件加载器。

    从 config/policies/ 目录加载 .rego 文件，
    按 package 名称组织策略。
    """

    def __init__(self, policies_dir: str = "config/policies"):
        self._policies_dir = policies_dir
        self._policies: dict[str, OpaPolicy] = {}
        self._loaded = False

    def load_all(self) -> dict[str, OpaPolicy]:
        """加载所有 .rego 文件。"""
        import os

        self._policies = {}
        if not os.path.isdir(self._policies_dir):
            logger.warning("policies dir not found: %s", self._policies_dir)
            return self._policies

        for fname in os.listdir(self._policies_dir):
            if not fname.endswith(".rego"):
                continue
            fpath = os.path.join(self._policies_dir, fname)
            try:
                with open(fpath) as f:
                    content = f.read()
                pkg = self._extract_package(content) or fname
                self._policies[pkg] = OpaPolicy(package=pkg, content=content, path=fpath)
                logger.info("loaded policy: %s (package=%s)", fname, pkg)
            except Exception as exc:
                logger.warning("failed to load policy %s: %s", fname, exc)

        self._loaded = True
        return self._policies

    def get(self, package: str) -> OpaPolicy | None:
        if not self._loaded:
            self.load_all()
        return self._policies.get(package)

    def list_packages(self) -> list[str]:
        if not self._loaded:
            self.load_all()
        return list(self._policies.keys())

    @staticmethod
    def _extract_package(content: str) -> str | None:
        import re

        m = re.search(r"^package\s+([\w.]+)", content, re.MULTILINE)
        if m:
            return m.group(1)
        return None
