"""apps/gateway/harness_routes.py — Phase R R3 Harness 能力反哺 API。

路由前缀：/internal/harness

接口：
    POST /internal/harness/capability-report  生成所有已 profile 模型的能力对比 Markdown 报告
    POST /internal/harness/run-profile        对指定模型跑 4 维度 benchmark 并入库
    GET  /internal/harness/profiles           列出所有模型的最新 capability profile
    GET  /internal/harness/profiles/{model_id} 获取指定模型最新 profile
    GET  /internal/harness/compare            对比两个模型的 capability
"""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_patch_tenant_limits

logger = logging.getLogger("ai_platform.gateway.harness_routes")

router = APIRouter(prefix="/internal/harness", tags=["harness"])


def _resolve(x_tenant_id: str | None, authorization: str | None) -> TenantRecord | JSONResponse:
    tenants = load_tenants()
    try:
        return resolve_tenant(x_tenant_id, authorization, tenants)
    except HTTPException as e:
        return json_error(int(e.status_code), "UNAUTHORIZED", str(e.detail))


def _require_admin(tenant: TenantRecord) -> JSONResponse | None:
    if not can_patch_tenant_limits(tenant.role):
        return json_error(403, "FORBIDDEN", "需要 platform_admin 角色")
    return None


def _get_store():
    """获取 CapabilityProfileStore；失败返回 None。"""
    try:
        from packages.agent.capability_profile import get_capability_profile_store

        return get_capability_profile_store()
    except Exception as exc:  # noqa: BLE001
        logger.warning("capability_profile store unavailable: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Markdown 报告生成
# ---------------------------------------------------------------------------


def _build_markdown_report(profiles: list[Any]) -> str:
    """从 profile 列表生成 Markdown 对比报告。"""
    if not profiles:
        return "# 模型能力报告\n\n> 暂无已 profile 的模型数据。\n"

    lines: list[str] = []
    lines.append("# 模型能力对比报告")
    lines.append("")
    lines.append(f"> 生成时间：{_fmt_ts(time.time())}  共 {len(profiles)} 个模型")
    lines.append("")

    # ---- 总览表 ----
    lines.append("## 总览")
    lines.append("")
    lines.append(
        "| 模型 | context_mgmt | long_memory | tool_use | planning | overall | 最强维度 | 最弱维度 |"
    )
    lines.append(
        "|------|:------------:|:-----------:|:--------:|:--------:|:-------:|:--------:|:--------:|"
    )

    for p in sorted(profiles, key=lambda x: x.scores.overall(), reverse=True):
        s = p.scores
        overall = s.overall()
        lines.append(
            f"| `{p.model_id}` "
            f"| {s.context_mgmt:.3f} "
            f"| {s.long_memory:.3f} "
            f"| {s.tool_use:.3f} "
            f"| {s.planning:.3f} "
            f"| **{overall:.3f}** "
            f"| {p.strength_dimension()} "
            f"| {p.weakness_dimension()} |"
        )

    lines.append("")

    # ---- 各模型详情 ----
    lines.append("## 各模型详情")
    lines.append("")
    for p in profiles:
        s = p.scores
        lines.append(f"### `{p.model_id}`")
        lines.append("")
        lines.append(f"- **Profile ID**: `{p.profile_id}`")
        lines.append(f"- **测评时间**: {_fmt_ts(p.timestamp)}")
        lines.append(f"- **综合得分**: {s.overall():.3f}")
        lines.append(f"- **最强维度**: {p.strength_dimension()}")
        lines.append(f"- **最弱维度**: {p.weakness_dimension()}")
        if p.notes:
            lines.append(f"- **备注**: {p.notes}")
        lines.append("")
        lines.append("| 维度 | 得分 | 评级 |")
        lines.append("|------|:----:|:----:|")
        for dim, score in s.to_dict().items():
            grade = _score_grade(score)
            lines.append(f"| {dim} | {score:.3f} | {grade} |")
        lines.append("")

    # ---- 任务推荐 ----
    lines.append("## 任务适用推荐")
    lines.append("")
    dim_labels = {
        "context_mgmt": ("长上下文处理", "context"),
        "long_memory": ("跨会话记忆检索", "memory"),
        "tool_use": ("工具调用 / Function Calling", "tool"),
        "planning": ("复杂任务规划", "planning"),
    }
    for field_name, (task_name, _dim_short) in dim_labels.items():
        best = max(profiles, key=lambda p: getattr(p.scores, field_name))
        score = getattr(best.scores, field_name)
        lines.append(f"- **{task_name}**：推荐 `{best.model_id}`（{field_name}={score:.3f}）")
    lines.append("")

    # ---- 降级链建议 ----
    if len(profiles) >= 2:
        sorted_profiles = sorted(profiles, key=lambda p: p.scores.overall(), reverse=True)
        lines.append("## 降级链建议")
        lines.append("")
        chain = " → ".join(f"`{p.model_id}`" for p in sorted_profiles)
        lines.append(f"综合能力降级链：{chain}")
        lines.append("")

    return "\n".join(lines)


def _fmt_ts(ts: float) -> str:
    """将 unix timestamp 格式化为可读字符串（避免 datetime.UTC 兼容问题）。"""
    import datetime

    return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S UTC")


def _score_grade(score: float) -> str:
    if score >= 0.9:
        return "优秀 ★★★★★"
    elif score >= 0.75:
        return "良好 ★★★★"
    elif score >= 0.6:
        return "中等 ★★★"
    elif score >= 0.4:
        return "偏弱 ★★"
    else:
        return "较差 ★"


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class RunProfileRequest(BaseModel):
    model_id: str = Field(..., min_length=1, description="要 benchmark 的模型 ID")
    mock: bool = Field(default=False, description="是否使用 mock 模式（不调用真实 LLM）")
    notes: str = Field(default="", description="备注")


class CapabilityReportRequest(BaseModel):
    format: str = Field(default="markdown", description="报告格式：markdown | json")
    include_models: list[str] = Field(default_factory=list, description="指定模型列表；空表示全部")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/capability-report")
async def generate_capability_report(
    body: CapabilityReportRequest = CapabilityReportRequest(),
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """生成所有已 profile 模型的能力对比报告（Markdown 或 JSON）。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    store = _get_store()
    if store is None:
        return json_error(503, "CAPABILITY_STORE_UNAVAILABLE", "capability profile store 不可用")

    all_profiles = store.list_all()
    if body.include_models:
        all_profiles = [p for p in all_profiles if p.model_id in body.include_models]

    if body.format == "json":
        return JSONResponse(
            {
                "profiles": [p.to_dict() for p in all_profiles],
                "count": len(all_profiles),
                "generated_at": time.time(),
            }
        )

    md = _build_markdown_report(all_profiles)
    return JSONResponse(
        {
            "report": md,
            "model_count": len(all_profiles),
            "format": "markdown",
            "generated_at": time.time(),
        }
    )


@router.post("/run-profile")
async def run_profile(
    body: RunProfileRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """对指定模型跑 4 维度 benchmark 并入库（admin only）。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err

    try:
        from packages.agent.capability_profile import run_capability_profile

        profile = await run_capability_profile(body.model_id, mock=body.mock)
        if body.notes:
            # 更新 notes（profile 是 dataclass，需要替换）
            from dataclasses import replace

            store = _get_store()
            if store:
                updated = replace(profile, notes=body.notes)
                # 重新存储（最新一条覆盖）
                store.store(updated)
                profile = updated

        return JSONResponse(profile.to_dict(), status_code=201)
    except Exception as exc:  # noqa: BLE001
        logger.exception("run_profile failed model=%s", body.model_id)
        return json_error(500, "BENCHMARK_FAILED", str(exc))


@router.get("/profiles")
async def list_profiles(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """列出所有模型的最新 capability profile。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    store = _get_store()
    if store is None:
        return json_error(503, "CAPABILITY_STORE_UNAVAILABLE", "capability profile store 不可用")

    profiles = store.list_all()
    return JSONResponse(
        {
            "profiles": [p.to_dict() for p in profiles],
            "count": len(profiles),
            "stats": store.stats(),
        }
    )


@router.get("/profiles/{model_id}")
async def get_profile(
    model_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """获取指定模型最新 capability profile。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    store = _get_store()
    if store is None:
        return json_error(503, "CAPABILITY_STORE_UNAVAILABLE", "capability profile store 不可用")

    profile = store.get_latest(model_id)
    if profile is None:
        return json_error(404, "PROFILE_NOT_FOUND", f"模型 {model_id} 没有 capability profile")
    return JSONResponse(profile.to_dict())


@router.get("/compare")
async def compare_models(
    m1: Annotated[str, Query(description="模型 1 ID")],
    m2: Annotated[str, Query(description="模型 2 ID")],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """对比两个模型的 capability profile。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    store = _get_store()
    if store is None:
        return json_error(503, "CAPABILITY_STORE_UNAVAILABLE", "capability profile store 不可用")

    result = store.compare(m1, m2)
    return JSONResponse(result)
