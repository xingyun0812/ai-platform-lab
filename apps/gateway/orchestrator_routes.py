"""控制流编排 REST API — Phase H #37

路由前缀：/internal/orchestrator

接口：
    POST   /internal/orchestrator/workflows                 创建工作流（admin）
    GET    /internal/orchestrator/workflows                 列出工作流
    GET    /internal/orchestrator/workflows/{workflow_id}   获取详情
    DELETE /internal/orchestrator/workflows/{workflow_id}   删除（admin）
    POST   /internal/orchestrator/workflows/{workflow_id}/execute  执行工作流
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.agent.orchestrator import (
    OrchestratorError,
    execute_workflow,
    parse_workflow,
)
from packages.agent.orchestrator.workflow_store import (
    WorkflowStore,
    get_workflow_store,
)

router = APIRouter(prefix="/internal/orchestrator", tags=["orchestrator"])


def _resolve(x_tenant_id: str | None, authorization: str | None) -> TenantRecord | JSONResponse:
    tenants = load_tenants()
    try:
        return resolve_tenant(x_tenant_id, authorization, tenants)
    except HTTPException as e:
        return json_error(int(e.status_code), "UNAUTHORIZED", str(e.detail))


def _require_admin(tenant: TenantRecord) -> JSONResponse | None:
    if tenant.role != "platform_admin":
        return json_error(403, "FORBIDDEN", "需要 platform_admin 角色")
    return None


def _store() -> WorkflowStore | JSONResponse:
    s = get_workflow_store()
    if s is None:
        return json_error(503, "ORCHESTRATOR_DISABLED", "ORCHESTRATOR_ENABLED=false")
    return s


class NodeSpec(BaseModel):
    node_id: str
    node_type: str
    config: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class EdgeSpec(BaseModel):
    from_node: str
    to_node: str
    condition: str | None = None


class WorkflowCreateRequest(BaseModel):
    workflow_id: str = Field(..., min_length=1)
    name: str = ""
    nodes: list[NodeSpec]
    edges: list[EdgeSpec]
    start_node: str
    end_node: str
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecuteRequest(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    max_steps: int | None = None
    timeout_seconds: float | None = None
    execution_id: str | None = Field(
        default=None,
        description="resume 时指定 checkpoint execution_id",
    )
    resume: bool = Field(default=False, description="从 checkpoint 继续执行")


@router.post("/workflows")
async def create_workflow(
    body: WorkflowCreateRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    wf_data = body.model_dump()
    try:
        wf = parse_workflow(wf_data)
    except Exception as e:
        return json_error(400, "VALIDATION_FAILED", str(e))
    existing = store.get_workflow(wf.workflow_id)
    if existing is not None:
        return json_error(409, "ALREADY_EXISTS", f"workflow {wf.workflow_id} 已存在")
    saved = store.add_workflow(wf, created_by=tenant.tenant_id)
    return JSONResponse(saved.to_dict(), status_code=201)


@router.get("/workflows")
async def list_workflows(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    wfs = store.list_workflows()
    return JSONResponse(
        {
            "workflows": [w.to_dict() for w in wfs],
            "count": len(wfs),
            "stats": store.stats(),
        }
    )


@router.get("/workflows/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    wf = store.get_workflow(workflow_id)
    if wf is None:
        return json_error(404, "NOT_FOUND", f"workflow {workflow_id} 不存在")
    return JSONResponse(wf.to_dict())


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(
    workflow_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    ok = store.remove_workflow(workflow_id)
    if not ok:
        return json_error(404, "NOT_FOUND", f"workflow {workflow_id} 不存在")
    return JSONResponse({"workflow_id": workflow_id, "deleted": True})


@router.post("/workflows/{workflow_id}/execute")
async def execute_workflow_api(
    workflow_id: str,
    body: ExecuteRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    wf = store.get_workflow(workflow_id)
    if wf is None:
        return json_error(404, "NOT_FOUND", f"workflow {workflow_id} 不存在")
    from apps.gateway.settings import get_settings

    settings = get_settings()
    merged_inputs = dict(body.inputs or {})
    merged_inputs.setdefault("tenant_id", tenant.tenant_id)
    merged_inputs.setdefault("allowed_tools", list(tenant.allowed_tools or ()))
    merged_inputs.setdefault("allowed_models", list(tenant.allowed_models or ()))
    try:
        if body.resume or settings.graph_checkpoint_enabled:
            from packages.agent.orchestrator.checkpoint_engine import (
                execute_workflow_checkpointed,
            )

            result = await execute_workflow_checkpointed(
                wf,
                tenant_id=tenant.tenant_id,
                inputs=merged_inputs,
                max_steps=body.max_steps or settings.orchestrator_max_steps,
                timeout_seconds=body.timeout_seconds or settings.orchestrator_timeout_seconds,
                execution_id=body.execution_id,
                resume=body.resume,
            )
        else:
            result = await execute_workflow(
                wf,
                inputs=merged_inputs,
                max_steps=body.max_steps or settings.orchestrator_max_steps,
                timeout_seconds=body.timeout_seconds or settings.orchestrator_timeout_seconds,
            )
    except OrchestratorError as e:
        return json_error(500, e.code, e.message)
    return JSONResponse(
        {
            "workflow_id": workflow_id,
            "execution_id": result.execution_id,
            "status": result.status,
            "final_output": result.final_output,
            "outputs": result.outputs,
            "trace": result.trace,
            "error": result.error,
            "execution_time_ms": round(result.execution_time_ms, 2),
        }
    )


@router.get("/executions/{execution_id}")
async def get_execution_checkpoint(
    execution_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    from packages.agent.graph_checkpoint import get_graph_checkpoint_store

    cp = get_graph_checkpoint_store().get(execution_id)
    if cp is None:
        return json_error(404, "NOT_FOUND", f"execution {execution_id} 不存在")
    if cp.tenant_id != tenant.tenant_id:
        return json_error(403, "FORBIDDEN", "租户不匹配")
    return JSONResponse(cp.to_dict())


@router.post("/executions/{execution_id}/resume")
async def resume_execution(
    execution_id: str,
    body: ExecuteRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    from packages.agent.graph_checkpoint import get_graph_checkpoint_store
    from packages.agent.orchestrator.checkpoint_engine import execute_workflow_checkpointed

    cp = get_graph_checkpoint_store().get(execution_id)
    if cp is None:
        return json_error(404, "NOT_FOUND", f"execution {execution_id} 不存在")
    if cp.tenant_id != tenant.tenant_id:
        return json_error(403, "FORBIDDEN", "租户不匹配")
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    wf = store.get_workflow(cp.workflow_id)
    if wf is None:
        return json_error(404, "NOT_FOUND", f"workflow {cp.workflow_id} 不存在")
    from apps.gateway.settings import get_settings

    settings = get_settings()
    try:
        result = await execute_workflow_checkpointed(
            wf,
            tenant_id=tenant.tenant_id,
            inputs=body.inputs or cp.inputs,
            max_steps=body.max_steps or settings.orchestrator_max_steps,
            timeout_seconds=body.timeout_seconds or settings.orchestrator_timeout_seconds,
            execution_id=execution_id,
            resume=True,
        )
    except OrchestratorError as e:
        return json_error(500, e.code, e.message)
    return JSONResponse(
        {
            "workflow_id": cp.workflow_id,
            "execution_id": result.execution_id,
            "status": result.status,
            "final_output": result.final_output,
            "outputs": result.outputs,
            "trace": result.trace,
            "error": result.error,
            "execution_time_ms": round(result.execution_time_ms, 2),
            "resumed": True,
        }
    )


@router.get("/examples")
async def list_examples() -> JSONResponse:
    """返回示例工作流模板（用于文档/引导）。"""
    return JSONResponse(
        {
            "examples": [
                {
                    "name": "简单线性",
                    "description": "start → llm_call → output → end",
                    "template": {
                        "workflow_id": "linear_example",
                        "name": "简单线性示例",
                        "nodes": [
                            {"node_id": "start", "node_type": "start"},
                            {
                                "node_id": "llm1",
                                "node_type": "llm_call",
                                "config": {"prompt": "你好，${input.name}"},
                            },
                            {
                                "node_id": "out",
                                "node_type": "output",
                                "config": {"value": "${llm1.content}"},
                            },
                            {"node_id": "end", "node_type": "end"},
                        ],
                        "edges": [
                            {"from_node": "start", "to_node": "llm1"},
                            {"from_node": "llm1", "to_node": "out"},
                            {"from_node": "out", "to_node": "end"},
                        ],
                        "start_node": "start",
                        "end_node": "end",
                    },
                },
                {
                    "name": "条件分支",
                    "description": "根据 LLM 输出选择不同后续",
                    "template": {
                        "workflow_id": "condition_example",
                        "name": "条件分支示例",
                        "nodes": [
                            {"node_id": "start", "node_type": "start"},
                            {
                                "node_id": "check",
                                "node_type": "condition",
                                "config": {
                                    "branches": [
                                        {"condition": '${llm1.content} == "yes"', "target": "yes_branch"},
                                    ],
                                    "default": "no_branch",
                                },
                            },
                            {"node_id": "yes_branch", "node_type": "output", "config": {"value": "yes"}},
                            {"node_id": "no_branch", "node_type": "output", "config": {"value": "no"}},
                            {"node_id": "end", "node_type": "end"},
                        ],
                        "edges": [
                            {"from_node": "start", "to_node": "check"},
                            {"from_node": "yes_branch", "to_node": "end"},
                            {"from_node": "no_branch", "to_node": "end"},
                        ],
                        "start_node": "start",
                        "end_node": "end",
                    },
                },
            ]
        }
    )
