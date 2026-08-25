"""调度策略模型与双轨声明加载（PRD #243，ADR-0009 #244）。

字段语义（ADR-0009 锁定）：
- ``mutex_group``: str | None — 同组工具不同时并行调度；None 表示无约束。
- ``priority``: int | None — 越大越优先；None 表示未显式声明（互斥裁决时仅当
  冲突双方都显式声明才择优，否则默认只拦截）。
- ``resource_pool``: "core" | "shared" — core 独占，shared 限流共享；未强制时
  解析为 "shared" 默认值。
- ``output_schema``: str | None — 输出 JSON Schema 摘要，结果聚合校验用。

声明双轨：YAML（``config/tool_classifications.yaml`` 扩展）优先，工具注册元数据
（``ToolDefinition`` 调度字段）兜底。未声明任何字段的工具解析为默认无约束策略，
行为与现状一致（向后兼容）。

本模块纯数据/解析，不依赖 LLM 与数据库，可独立单测。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TOOL_CLASSIFICATIONS_PATH = REPO_ROOT / "config" / "tool_classifications.yaml"

# 调度字段白名单（ADR-0009）
_SCHEDULING_KEYS = ("mutex_group", "priority", "resource_pool", "output_schema")

# 资源池合法值；未声明时落的默认值
_RESOURCE_POOLS = frozenset({"core", "shared"})
_DEFAULT_RESOURCE_POOL = "shared"


class SchedulePolicyError(ValueError):
    """调度配置解析/校验错误。"""


@dataclass(frozen=True)
class SchedulingPolicy:
    """单个工具解析后的调度策略（已合并 YAML + 注册元数据，双轨完成）。"""

    mutex_group: str | None = None
    priority: int | None = None
    resource_pool: str = _DEFAULT_RESOURCE_POOL
    output_schema: str | None = None

    @property
    def is_constrained(self) -> bool:
        """是否声明了任一调度约束（互斥组/优先级/输出 schema）。"""
        return (
            self.mutex_group is not None
            or self.priority is not None
            or self.output_schema is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mutex_group": self.mutex_group,
            "priority": self.priority,
            "resource_pool": self.resource_pool,
            "output_schema": self.output_schema,
        }


def _extract_policy(raw: dict[str, Any] | None) -> dict[str, Any]:
    """从任意来源提取调度字段并规范化（resource_pool 校验合法值）。"""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    mg = raw.get("mutex_group")
    if mg is not None and str(mg).strip():
        out["mutex_group"] = str(mg)
    pri = raw.get("priority")
    if pri is not None:
        try:
            out["priority"] = int(pri)
        except (TypeError, ValueError) as exc:
            raise SchedulePolicyError(f"priority 必须为整数，got {pri!r}") from exc
    pool = raw.get("resource_pool")
    if pool is not None:
        pool_s = str(pool).strip()
        if pool_s not in _RESOURCE_POOLS:
            raise SchedulePolicyError(
                f"resource_pool 必须为 {'/'.join(sorted(_RESOURCE_POOLS))}，got {pool_s!r}"
            )
        out["resource_pool"] = pool_s
    os_ = raw.get("output_schema")
    if os_ is not None and str(os_).strip():
        out["output_schema"] = str(os_)
    return out


def load_scheduling_config(
    path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """从 tool_classifications.yaml 加载每工具的调度字段（YAML 轨）。

    Returns: {tool_name: {mutex_group?, priority?, resource_pool?, output_schema?}}
    仅含声明了任一调度字段的工具。
    """
    cfg_path = Path(path) if path else DEFAULT_TOOL_CLASSIFICATIONS_PATH
    if not cfg_path.is_file():
        return {}
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    entries = data.get("classifications")
    if not isinstance(entries, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        tool_name = item.get("tool_name")
        if not tool_name:
            continue
        policy = _extract_policy(item)
        if policy:
            out[str(tool_name)] = policy
    return out


def merge_tool_policy(
    yaml_entry: dict[str, Any] | None,
    tool_metadata: dict[str, Any] | None,
) -> SchedulingPolicy:
    """双轨合并：YAML 优先，``ToolDefinition`` 注册元数据兜底。

    逐字段：YAML 声明了就用 YAML；否则回落到注册元数据；都未声明则保持默认。
    """
    merged: dict[str, Any] = {}
    y = _extract_policy(yaml_entry)
    t = _extract_policy(tool_metadata)
    for key in _SCHEDULING_KEYS:
        if key in y:
            merged[key] = y[key]
        elif key in t:
            merged[key] = t[key]
    return SchedulingPolicy(
        mutex_group=merged.get("mutex_group"),
        priority=merged.get("priority"),
        resource_pool=merged.get("resource_pool", _DEFAULT_RESOURCE_POOL),
        output_schema=merged.get("output_schema"),
    )


class SchedulePolicyStore:
    """调度策略仓库：聚合 YAML 配置 + 工具注册元数据，按工具名解析。

    供下游切片（mutex / resource_pool / aggregator，#246/#247/#248）读取
    ``resolve(name)`` 拿到合并后的 ``SchedulingPolicy``。
    """

    def __init__(
        self,
        *,
        path: str | Path | None = None,
        registry: Any | None = None,
        config: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._yaml_policies: dict[str, dict[str, Any]] = (
            dict(config) if config is not None else load_scheduling_config(path)
        )
        self._registry = registry

    def resolve(self, tool_name: str) -> SchedulingPolicy:
        """解析单个工具的合并策略（YAML 优先、注册元数据兜底）。"""
        yaml_entry = self._yaml_policies.get(tool_name)
        tool_meta: Any = None
        if self._registry is not None:
            tool = self._registry.get(tool_name)
            if tool is not None:
                meta = getattr(tool, "scheduling_metadata", None)
                tool_meta = meta() if callable(meta) else None
        return merge_tool_policy(yaml_entry, tool_meta)

    def resolve_many(self, tool_names: Iterable[str]) -> dict[str, SchedulingPolicy]:
        return {name: self.resolve(name) for name in tool_names}

    def mutex_groups(self) -> dict[str, list[str]]:
        """互斥组 → 工具列表（供互斥裁决 #246）。仅含 YAML 中声明了互斥组的工具。"""
        groups: dict[str, list[str]] = {}
        for tool_name, yaml_entry in self._yaml_policies.items():
            if yaml_entry.get("mutex_group"):
                groups.setdefault(str(yaml_entry["mutex_group"]), []).append(tool_name)
        return groups

    @property
    def yaml_policies(self) -> dict[str, dict[str, Any]]:
        """YAML 声明的调度字段（只读视图，测试/日志用）。"""
        return dict(self._yaml_policies)


def resolve_scheduling_policy(
    tool_name: str,
    *,
    store: SchedulePolicyStore | None = None,
    yaml_entry: dict[str, Any] | None = None,
    tool_metadata: dict[str, Any] | None = None,
) -> SchedulingPolicy:
    """便捷函数：单次解析。传入 store 则走 store.resolve，否则直接合并传入源。"""
    if store is not None:
        return store.resolve(tool_name)
    return merge_tool_policy(yaml_entry, tool_metadata)