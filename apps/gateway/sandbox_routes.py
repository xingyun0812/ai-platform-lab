"""沙箱容器隔离 REST API — Phase I #41

路由前缀：/internal/sandbox

接口：
    GET    /internal/sandbox/profiles              列出所有配置档案
    GET    /internal/sandbox/profiles/{profile_id} 获取档案详情
    POST   /internal/sandbox/profiles              注册档案（admin）
    DELETE /internal/sandbox/profiles/{profile_id} 删除档案（admin）
    POST   /internal/sandbox/execute               执行命令（admin）
    GET    /internal/sandbox/status                检查 docker/gvisor 可用性
"""

from __future__ import annotations

import asyncio
import shutil
import time
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_patch_tenant_limits
from packages.sandbox.executor import (
    SandboxConfig,
    SandboxProfile,
    get_sandbox_executor,
    init_sandbox_executor,
)

router = APIRouter(prefix="/internal/sandbox", tags=["sandbox"])


# --------------------------------------------------------------------------- #
# 辅助函数
# --------------------------------------------------------------------------- #


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


def _executor_or_503():
    exec_ = get_sandbox_executor()
    if exec_ is None:
        # 尝试懒初始化（不加载配置文件）
        try:
            init_sandbox_executor()
            exec_ = get_sandbox_executor()
        except Exception:
            pass
    if exec_ is None:
        return json_error(503, "SANDBOX_DISABLED", "沙箱执行器未初始化，请设置 SANDBOX_ENABLED=true")
    return exec_


# --------------------------------------------------------------------------- #
# Pydantic 请求体模型
# --------------------------------------------------------------------------- #


class ProfileCreateRequest(BaseModel):
    profile_id: str = Field(..., min_length=1)
    name: str = ""
    seccomp_rules: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    readonly_paths: list[str] = Field(default_factory=list)
    writable_paths: list[str] = Field(default_factory=list)
    network_enabled: bool = False


class SandboxConfigRequest(BaseModel):
    enabled: bool = True
    runtime: str = "process"
    image: str = "python:3.11-slim"
    memory_limit_mb: int = 256
    cpu_limit: float = 0.5
    timeout_seconds: float = 30.0
    profile_id: str = "default"
    env: dict[str, str] = Field(default_factory=dict)


class ExecuteRequest(BaseModel):
    command: list[str] = Field(..., min_length=1)
    config: SandboxConfigRequest = Field(default_factory=SandboxConfigRequest)


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #


@router.get("/profiles")
async def list_profiles(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    exec_ = _executor_or_503()
    if isinstance(exec_, JSONResponse):
        return exec_
    profiles = exec_.list_profiles()
    return JSONResponse(
        {
            "profiles": [p.to_dict() for p in profiles],
            "total": len(profiles),
        }
    )


@router.get("/profiles/{profile_id}")
async def get_profile(
    profile_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    exec_ = _executor_or_503()
    if isinstance(exec_, JSONResponse):
        return exec_
    profile = exec_.get_profile(profile_id)
    if profile is None:
        return json_error(404, "NOT_FOUND", f"沙箱档案 {profile_id} 不存在")
    return JSONResponse(profile.to_dict())


@router.post("/profiles")
async def create_profile(
    body: ProfileCreateRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    exec_ = _executor_or_503()
    if isinstance(exec_, JSONResponse):
        return exec_
    existing = exec_.get_profile(body.profile_id)
    if existing is not None:
        return json_error(409, "ALREADY_EXISTS", f"沙箱档案 {body.profile_id} 已存在")
    profile = SandboxProfile(
        profile_id=body.profile_id,
        name=body.name or body.profile_id,
        seccomp_rules=body.seccomp_rules,
        capabilities=body.capabilities,
        readonly_paths=body.readonly_paths,
        writable_paths=body.writable_paths,
        network_enabled=body.network_enabled,
        created_at=time.time(),
    )
    exec_.register_profile(profile)
    return JSONResponse(profile.to_dict(), status_code=201)


@router.delete("/profiles/{profile_id}")
async def delete_profile(
    profile_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    exec_ = _executor_or_503()
    if isinstance(exec_, JSONResponse):
        return exec_
    ok = exec_.remove_profile(profile_id)
    if not ok:
        return json_error(404, "NOT_FOUND", f"沙箱档案 {profile_id} 不存在")
    return JSONResponse({"profile_id": profile_id, "deleted": True})


@router.post("/execute")
async def execute_command(
    body: ExecuteRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """在沙箱中执行命令（仅限 admin）。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    exec_ = _executor_or_503()
    if isinstance(exec_, JSONResponse):
        return exec_

    if not body.command:
        return json_error(400, "INVALID_COMMAND", "command 不能为空")

    cfg_req = body.config
    config = SandboxConfig(
        enabled=cfg_req.enabled,
        runtime=cfg_req.runtime,
        image=cfg_req.image,
        memory_limit_mb=cfg_req.memory_limit_mb,
        cpu_limit=cfg_req.cpu_limit,
        timeout_seconds=min(cfg_req.timeout_seconds, 300.0),  # 最大 5 分钟
        profile_id=cfg_req.profile_id,
        env=cfg_req.env,
    )

    if cfg_req.runtime not in ("process", "docker", "gvisor"):
        return json_error(400, "INVALID_RUNTIME", "runtime 必须是 process/docker/gvisor")

    try:
        result = await exec_.execute(body.command, config)
    except Exception as exc:
        return json_error(500, "EXECUTION_ERROR", str(exc))

    return JSONResponse(result.to_dict())


@router.get("/status")
async def sandbox_status(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """检查 docker 和 gvisor 是否可用。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    exec_ = get_sandbox_executor()

    docker_available = shutil.which("docker") is not None
    gvisor_available = False

    if docker_available:
        # 检查 gVisor runsc
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "run", "--rm", "--runtime=runsc", "hello-world",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=5.0)
                gvisor_available = proc.returncode == 0
            except asyncio.TimeoutError:
                pass
        except Exception:
            pass

    return JSONResponse(
        {
            "executor_initialized": exec_ is not None,
            "docker_available": docker_available,
            "gvisor_available": gvisor_available,
            "profiles_count": len(exec_.list_profiles()) if exec_ else 0,
        }
    )
