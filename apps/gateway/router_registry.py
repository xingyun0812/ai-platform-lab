"""Gateway router 注册表 — 统一 mount 所有 APIRouter（Issue #156 PR-1）。"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from apps.gateway.agent.approval_routes import router as agent_approval_router
from apps.gateway.agent.execution_handle_routes import router as execution_handle_router
from apps.gateway.agent.long_run_routes import router as long_run_router
from apps.gateway.agent.plan_approval_routes import router as plan_approval_router
from apps.gateway.agent.plan_workflow_routes import router as plan_workflow_router
from apps.gateway.agent.routes import router as agent_router
from apps.gateway.agent.strategy_patch_routes import router as strategy_patch_router
from apps.gateway.agent_lifecycle_routes import router as agent_lifecycle_router
from apps.gateway.audit_action_routes import router as audit_action_router
from apps.gateway.audit_routes import router as audit_router
from apps.gateway.auth_routes import router as auth_router
from apps.gateway.billing_routes import router as billing_router
from apps.gateway.console_routes import rag_router as console_rag_router
from apps.gateway.console_routes import router as console_router
from apps.gateway.embedding_routes import router as embedding_router
from apps.gateway.feedback_loop_routes import router as feedback_loop_router
from apps.gateway.feedback_routes import router as feedback_router
from apps.gateway.harness_routes import router as harness_router
from apps.gateway.hitl_routes import router as hitl_router
from apps.gateway.mcp_routes import router as mcp_router
from apps.gateway.memory_routes import router as memory_router
from apps.gateway.multi_agent_routes import router as multi_agent_router
from apps.gateway.orchestrator_routes import router as orchestrator_router
from apps.gateway.pii_routes import router as pii_router
from apps.gateway.platform_routes import router as platform_router
from apps.gateway.prompt_experiment_routes import router as prompt_experiment_router
from apps.gateway.prompt_routes import router as prompt_router
from apps.gateway.quality_routes import router as quality_router
from apps.gateway.rag.query_routes import router as rag_query_router
from apps.gateway.rag.routes import router as rag_router
from apps.gateway.sandbox_routes import router as sandbox_router
from apps.gateway.storage_routes import router as storage_router

logger = logging.getLogger("ai_platform.gateway.router_registry")


def mount_gateway_routers(app: FastAPI) -> None:
    """挂载全部 gateway 路由（含此前漏挂的 long_run / harness）。"""
    for router in (
        rag_router,
        rag_query_router,
        agent_router,
        agent_approval_router,
        strategy_patch_router,
        plan_workflow_router,
        plan_approval_router,
        execution_handle_router,
        long_run_router,
        audit_router,
        audit_action_router,
        auth_router,
        billing_router,
        embedding_router,
        hitl_router,
        mcp_router,
        memory_router,
        multi_agent_router,
        orchestrator_router,
        pii_router,
        platform_router,
        prompt_router,
        prompt_experiment_router,
        agent_lifecycle_router,
        sandbox_router,
        storage_router,
        feedback_router,
        quality_router,
        feedback_loop_router,
        harness_router,
        console_router,
        console_rag_router,
    ):
        app.include_router(router)
    _mount_console_static(app)


def _mount_console_static(app: FastAPI) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    console_static = repo_root / "apps" / "console" / "static"
    if console_static.is_dir() and (console_static / "index.html").is_file():
        app.mount("/console", StaticFiles(directory=str(console_static), html=True), name="console")
        return
    console_dir = repo_root / "apps" / "console"
    if console_dir.is_dir():
        app.mount("/console", StaticFiles(directory=str(console_dir), html=True), name="console")
