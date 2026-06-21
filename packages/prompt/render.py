"""Prompt 模板渲染 — {{var}} 双花括号语法。

不使用 str.format()（会与 RAG 模板现有的 {context}/{query} 冲突），
也不使用 string.Template（$var 语法不直观）。

支持：
    {{var_name}}          → variables["var_name"]
    {{ var_name }}        → 允许两端空格
    未找到变量             → 保持原样 {{var_name}}（开发期可视化错误）
"""

from __future__ import annotations

import re
from typing import Any

_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def extract_variables(template: str) -> list[str]:
    """从模板中提取所有变量名（去重保序）。"""
    seen: set[str] = set()
    out: list[str] = []
    for m in _PATTERN.finditer(template):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def render(template: str, variables: dict[str, Any] | None = None) -> str:
    """渲染模板；未提供变量的占位符保持原样。"""
    if not variables:
        return template

    def _replace(match: re.Match) -> str:
        name = match.group(1)
        if name in variables:
            return str(variables[name])
        return match.group(0)

    return _PATTERN.sub(_replace, template)


def validate_template(template: str, required_vars: list[str]) -> list[str]:
    """校验模板是否包含必需变量；返回缺失变量列表。"""
    actual = set(extract_variables(template))
    return [v for v in required_vars if v not in actual]
