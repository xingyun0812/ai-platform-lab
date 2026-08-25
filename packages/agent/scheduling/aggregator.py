"""结果聚合校验与缺失补全（PRD #243，ADR-0009 #244，#248）。

在 parallel 分支 / 多工具执行完成后统一聚合：按各工具声明的 ``output_schema``
校验产物完整性。

- **完整性校验**：``output_schema`` 为每个工具声明的必需输出字段名 → 期望类型的
  紧凑声明（如 ``{"result": "number"}``，见 ``config/tool_classifications.yaml``）。
  校验仅要求产物 dict 中这些字段存在且非 None（**缺失/失败可识别**）。不校验具体
  类型（简化声明里类型仅作注释，避免与运行时实际值产生误判）。
- **缺失补全**：缺失或失败的产物触发补全回调（``completions_cb``），重试指定次数
  （``completion_attempts``，默认 1）。补全回调返回新的产物 dict 或 None（仍失败）。
- **聚合错误不阻塞成功分支**：补全后仍失败的产物标记到 trace，聚合结果返回
  ``errors`` 汇总，但成功产物完整返回、不整体丢弃（部分成功保留）。
- **向后兼容**：未声明 ``output_schema`` 的工具不校验、不补全，产物按原样纳入
  完整结果，行为与现状一致。

本模块纯函数 + 可选补全回调，零外部依赖（不依赖 LLM/DB/yaml），可独立单测。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

# 补全回调签名：给定工具名与旧产物（dict 或 None）→ 返回新产物或 None。
CompletionCallback = Callable[[str, Any], Awaitable[Any] | Any]


@dataclass(frozen=True)
class AggregatedProduct:
    """聚合后的单个工具产物（含完整性状态）。"""

    tool_name: str
    output: Any
    status: str  # complete | incomplete | failed
    missing_fields: tuple[str, ...] = ()
    attempts: int = 0  # 实际补全尝试次数


@dataclass
class AggregationResult:
    """一次聚合的完整结果。只含声明 output_schema 的工具的校验信息。"""

    products: list[AggregatedProduct]  # 按输入顺序
    # 补全后仍失败（或缺失）的产物，标记到 trace 用。
    failed: list[AggregatedProduct] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # 聚合错误汇总，不阻塞成功分支
    attempts: int = 0  # 全局补全尝试次数

    @property
    def has_failures(self) -> bool:
        return bool(self.failed)

    def full_outputs(self) -> dict[str, Any]:
        """完整产物 dict（tool_name → output），部分失败不整体丢弃（AC4）。"""
        return {p.tool_name: p.output for p in self.products if p.status != "failed"}


class OutputSchema:
    """``output_schema`` 的运行时解析与完整性校验。"""

    def __init__(self, raw: str | Mapping[str, Any] | None) -> None:
        if raw is None:
            self._required: dict[str, str] = {}
            return
        if isinstance(raw, Mapping):
            declared = dict(raw)
        else:
            try:
                parsed: Any = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                declared = {}
            else:
                declared = parsed if isinstance(parsed, Mapping) else {}
        # 只保留字段名 → 类型字符串的声明（类型仅作注释，不参与校验）。
        self._required = {
            str(k): str(v)
            for k, v in declared.items()
            if not (k.startswith("$") or k in ("type", "properties"))
        }

    @property
    def declared(self) -> bool:
        return bool(self._required)

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(self._required)

    def missing(self, output: Any) -> tuple[str, ...]:
        """返回产物中缺失（键不存在或值为 None）的必需字段。"""
        if not self._required:
            return ()
        if not isinstance(output, Mapping):
            return tuple(self._required)
        return tuple(
            f for f in self._required if output.get(f) is None
        )


def parse_output_schema(raw: str | Mapping[str, Any] | None) -> OutputSchema:
    """便捷工厂：解析任意 output_schema 输入。"""
    return OutputSchema(raw)


async def validate_completeness(
    tool_name: str,
    output: Any,
    raw_schema: str | Mapping[str, Any] | None,
) -> AggregatedProduct:
    """按输出 schema 校验单个工具产物的完整性。

    未声明 schema 的工具：不做校验，产物标记为 complete（向后兼容）。
    """
    schema = OutputSchema(raw_schema)
    if not schema.declared:
        return AggregatedProduct(tool_name, output, "complete")
    missing = schema.missing(output)
    if missing:
        return AggregatedProduct(tool_name, output, "incomplete", missing)
    return AggregatedProduct(tool_name, output, "complete")


async def aggregate_tool_outputs(
    results: Sequence[Mapping[str, Any]],
    *,
    schemas: Mapping[str, str | Mapping[str, Any] | None] | Callable[[str], Any],
    completions_cb: CompletionCallback | None = None,
    completion_attempts: int = 1,
) -> AggregationResult:
    """对一次 parallel / 多工具执行的结果按各自 schema 聚合校验。

    Args:
        results: 工具执行结果列表。每个元素至少含 ``tool_name``；产物可从
            ``output`` 或 ``result`` 键提取（兼容 tool_call 与 parallel 分支形态）。
        schemas: 工具名 → 声明的 output_schema。可传 dict 或 ``resolve(schemas)``
            回调（生产可传 ``SchedulePolicyStore.resolve``）。
        completions_cb: 可选补全回调。对缺失/失败产物调用，返回新产物或 None。
        completion_attempts: 每个缺失/失败产物的最大补全尝试次数，默认 1。

    Returns:
        AggregationResult。失败/缺失产物在 ``failed``，成功产物在 ``products`` 且
        通过 ``full_outputs()`` 完整返回（部分成功不丢弃）。
    """
    max_attempts = max(0, int(completion_attempts))

    def _resolve_schema(tool: str) -> Any:
        if callable(schemas):
            return schemas(tool)
        return schemas.get(tool) if isinstance(schemas, Mapping) else None

    products: list[AggregatedProduct] = []
    failed: list[AggregatedProduct] = []
    errors: list[str] = []
    total_attempts = 0

    for raw in results:
        if not isinstance(raw, Mapping):
            continue
        tool_name = str(raw.get("tool_name", ""))
        # 提取产物：优先 output，其次 result；两者皆无视为执行失败（无产出）。
        output = raw.get("output", raw.get("result"))
        raw_schema = _resolve_schema(tool_name)
        schema = OutputSchema(raw_schema)

        # 无产出但声明了 schema → 直接视为失败，进入补全。
        status = (
            "failed"
            if output is None and schema.declared
            else "complete"
        )
        missing: tuple[str, ...] = ()
        if schema.declared and output is not None:
            missing = schema.missing(output)
            if missing:
                status = "incomplete"

        attempts = 0
        # 对缺失/失败产物触发补全重试。
        while (
            (status == "incomplete" or status == "failed")
            and completions_cb is not None
            and attempts < max_attempts
        ):
            try:
                replaced: Any = completions_cb(tool_name, output)
                if hasattr(replaced, "__await__"):
                    replaced = await replaced  # type: ignore[misc]
            except Exception as exc:  # noqa: BLE001 — 补全失败视为本次补全未成功
                replaced = None
                errors.append(f"tool={tool_name} completion error: {exc}")
            attempts += 1
            total_attempts += 1
            if replaced is None:
                status = "failed"
                break
            output = replaced
            missing = schema.missing(output)
            if not missing:
                status = "complete"
            else:
                status = "incomplete"

        if status == "complete":
            products.append(AggregatedProduct(tool_name, output, "complete", attempts=attempts))
            continue

        # 补全后仍失败/缺失 → 标记到 trace（failed 列表），不阻塞成功分支。
        prod = AggregatedProduct(tool_name, output, status, missing, attempts)
        products.append(prod)
        failed.append(prod)
        errors.append(
            f"tool={tool_name} product {status}: missing={list(missing)}"
        )

    return AggregationResult(
        products=products,
        failed=failed,
        errors=errors,
        attempts=total_attempts,
    )