from __future__ import annotations

import logging
from typing import Any

from packages.opa.loader import OpaLoader, OpaPolicy

logger = logging.getLogger("ai_platform.opa.evaluator")


class OpaEvaluator:
    """OPA 策略评估引擎。

    使用内置的简化 Rego 评估器（不依赖 opa-python 库）。
    支持基本的 allow/deny 规则评估。
    """

    def __init__(self, loader: OpaLoader):
        self._loader = loader

    async def check(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """评估策略输入，返回决策结果。

        Args:
            input_data: 策略输入，包含 tenant_id, role, path, method, 等

        Returns:
            {"allow": bool, "reason": str, "policy": str}
        """
        policies = self._loader.load_all()
        if not policies:
            return {"allow": True, "reason": "no policies loaded", "policy": ""}

        # 评估所有策略：任何一个 deny 则整体 deny
        any_allow = False
        has_policy = False
        last_reason = "no matching policy rule"
        last_policy = "default"

        for _pkg_name, policy in sorted(policies.items()):
            result = self._evaluate_policy(policy, input_data)
            if result is None:
                continue
            has_policy = True
            if not result.get("allow", True):
                return {
                    "allow": False,
                    "reason": result.get("reason", "denied by policy"),
                    "policy": result.get("policy", _pkg_name),
                }
            if result.get("allow"):
                any_allow = True
                last_reason = result.get("reason", "allowed by policy")
                last_policy = result.get("policy", _pkg_name)

        if has_policy and any_allow:
            return {"allow": True, "reason": last_reason, "policy": last_policy}

        if has_policy:
            return {"allow": False, "reason": "denied by all policies", "policy": "all"}

        return {"allow": True, "reason": "no matching policy rule", "policy": "default"}

    def _evaluate_policy(
        self,
        policy: OpaPolicy,
        input_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """评估单条 Rego 策略。

        使用简化的规则匹配：
        - default allow = false → 默认拒绝
        - allow { condition1; condition2 } → 所有条件满足时允许
        - startswith(field, prefix) → 字符串前缀匹配
        - field == "value" → 精确匹配
        """
        content = policy.content

        # 检查是否有 default allow = false
        has_default_deny = "default allow = false" in content or "default allow:=false" in content

        # 提取 allow 规则
        import re

        allow_rules = re.findall(
            r"allow\s*\{[^}]+}", content, re.DOTALL
        )

        if not allow_rules and not has_default_deny:
            return None  # 不相关策略

        for rule in allow_rules:
            if self._match_rule(rule, input_data):
                reason = self._extract_reason(rule, input_data)
                return {
                    "allow": True,
                    "reason": reason or "allowed by policy",
                    "policy": policy.package,
                }

        if has_default_deny:
            return {
                "allow": False,
                "reason": "denied by default",
                "policy": policy.package,
            }

        return None

    def _match_rule(self, rule: str, input_data: dict[str, Any]) -> bool:
        """匹配单条规则中的条件。"""
        import re

        # 提取大括号内的条件
        m = re.search(r"\{(.+)", rule, re.DOTALL)
        if not m:
            return False
        body = m.group(1).strip().rstrip("}").strip()

        # 按分号或换行分割条件
        conditions = re.split(r";\s*|\n+", body)

        for cond in conditions:
            cond = cond.strip()
            if not cond:
                continue

            # startswith(field, prefix)
            sw = re.match(r"startswith\(\s*(\w+)\s*,\s*\"([^\"]+)\"\s*\)", cond)
            if sw:
                field = sw.group(1)
                prefix = sw.group(2)
                val = input_data.get(field, "")
                if not isinstance(val, str) or not val.startswith(prefix):
                    return False
                continue

            # field == "value" or field = "value"
            eq = re.match(r"(\w+)\s*(?:==|=)\s*\"([^\"]+)\"", cond)
            if eq:
                field = eq.group(1)
                expected = eq.group(2)
                val = input_data.get(field, "")
                if str(val) != expected:
                    return False
                continue

            # input.field == "value"
            ieq = re.match(r"input\.(\w+)\s*(?:==|=)\s*\"([^\"]+)\"", cond)
            if ieq:
                field = ieq.group(1)
                expected = ieq.group(2)
                val = input_data.get(field, "")
                if str(val) != expected:
                    return False
                continue

        return True

    @staticmethod
    def _extract_reason(rule: str, input_data: dict[str, Any]) -> str | None:
        """从规则中提取原因说明（注释）。"""
        for line in rule.split("\n"):
            line = line.strip()
            if line.startswith("#"):
                return line.lstrip("# ").strip()
        return None
