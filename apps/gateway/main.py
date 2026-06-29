from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from apps.gateway.agent.approval_routes import router as agent_approval_router
from apps.gateway.agent.plan_approval_routes import router as plan_approval_router
from apps.gateway.agent.plan_workflow_routes import router as plan_workflow_router
from apps.gateway.agent.routes import router as agent_router
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
from apps.gateway.hitl_routes import router as hitl_router
from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.mcp_routes import router as mcp_router
from apps.gateway.memory_routes import router as memory_router
from apps.gateway.model_router import forward_with_model_router
from apps.gateway.platform_adapter import wire_platform
from apps.gateway.multi_agent_routes import router as multi_agent_router
from apps.gateway.orchestrator_routes import router as orchestrator_router
from apps.gateway.pii_routes import router as pii_router
from apps.gateway.platform_routes import router as platform_router
from apps.gateway.prompt_experiment_routes import router as prompt_experiment_router
from apps.gateway.prompt_routes import router as prompt_router
from apps.gateway.quality_routes import router as quality_router
from apps.gateway.quota import get_quota_tracker
from apps.gateway.rag.query_routes import router as rag_query_router
from apps.gateway.rag.routes import router as rag_router
from apps.gateway.request_guards import check_model_allowed, check_rate_limit, check_token_budget
from apps.gateway.sandbox_routes import router as sandbox_router
from apps.gateway.settings import get_settings
from apps.gateway.storage_routes import router as storage_router
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.agent.registry import build_default_registry
from packages.billing.budget import budget_platform_meta, get_budget_snapshot
from packages.billing.recorder import record_upstream_usage
from packages.contracts.schemas import ChatCompletionRequest
from packages.mcp import init_mcp_registry
from packages.memory import init_memory_store
from packages.observability.context import get_trace_id
from packages.observability.metrics import get_metrics_store
from packages.observability.middleware import TraceIdMiddleware
from packages.observability.otel import init_otel
from packages.prompt import init_experiment_store as init_prompt_experiment_store
from packages.prompt import init_registry as init_prompt_registry
from packages.semantic_cache import (
    SemanticCacheConfig,
    get_semantic_cache,
    init_semantic_cache,
)

logger = logging.getLogger("ai_platform.gateway")

quota_tracker = get_quota_tracker()


def _build_semantic_cache_config(settings) -> SemanticCacheConfig:
    skip_models_str = (settings.semantic_cache_skip_models or "").strip()
    skip_models = [m.strip() for m in skip_models_str.split(",") if m.strip()]
    return SemanticCacheConfig(
        enabled=settings.semantic_cache_enabled,
        mode=settings.semantic_cache_mode,
        similarity_threshold=settings.semantic_cache_similarity_threshold,
        ttl_seconds=settings.semantic_cache_ttl_seconds,
        max_entries_per_tenant=settings.semantic_cache_max_entries_per_tenant,
        skip_models=skip_models,
        max_temperature=settings.semantic_cache_max_temperature,
        embedding_dims=settings.embedding_dimensions,
    )


def get_tenants() -> dict[str, TenantRecord]:
    return load_tenants()


def create_app() -> FastAPI:
    settings = get_settings()
    wire_platform()
    init_otel(
        service_name=settings.app_name,
        enabled=settings.otel_enabled,
        console_export=settings.otel_console_export,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
    )
    # Phase G — 语义缓存初始化（启用时根据 REDIS_URL 选择后端）
    if settings.semantic_cache_enabled:
        init_semantic_cache(
            _build_semantic_cache_config(settings),
            redis_url=settings.redis_url or None,
        )
        logger.info(
            "semantic cache enabled mode=%s threshold=%.2f ttl=%ds",
            settings.semantic_cache_mode,
            settings.semantic_cache_similarity_threshold,
            settings.semantic_cache_ttl_seconds,
        )
    # Phase F — Prompt 版本注册表初始化
    if settings.prompt_registry_enabled:
        init_prompt_registry(
            yaml_path=settings.prompts_config_path,
            overrides_path=settings.prompt_overrides_path,
            legacy_fallback={
                # 向后兼容：若 prompts.yaml 中无 rag_query，回退到原 txt
                "rag_query": settings.rag_prompt_path,
            },
        )
        logger.info(
            "prompt registry enabled yaml=%s overrides=%s",
            settings.prompts_config_path,
            settings.prompt_overrides_path,
        )
        # Phase F #30 — A/B 实验存储
        if settings.prompt_experiment_enabled:
            init_prompt_experiment_store(
                storage_path=settings.prompt_experiments_path
            )
            logger.info(
                "prompt experiment enabled storage=%s",
                settings.prompt_experiments_path,
            )
    # Phase F #31 — 长记忆持久化
    if settings.memory_store_enabled:
        init_memory_store(database_url=settings.database_url or None)
        logger.info(
            "memory store enabled database_url=%s",
            "configured" if settings.database_url else "memory-fallback",
        )
    # Phase F #32 — MCP 真实集成
    if settings.mcp_enabled:
        init_mcp_registry(
            yaml_path=settings.mcp_servers_config_path,
            overrides_path=settings.mcp_overrides_path,
        )
        logger.info(
            "mcp enabled yaml=%s overrides=%s",
            settings.mcp_servers_config_path,
            settings.mcp_overrides_path,
        )
    # Phase O #90 — Plugin Manifest
    if settings.agent_plugins_enabled:
        from packages.agent.plugins.loader import get_loaded_plugins

        reserved = frozenset(build_default_registry().keys())
        plugins = get_loaded_plugins(reserved_names=reserved)
        logger.info(
            "agent plugins enabled dir=%s count=%d",
            settings.agent_plugins_config_dir,
            len(plugins),
        )
    # Phase H #37 — 控制流编排引擎
    if settings.orchestrator_enabled:
        from packages.agent.orchestrator import init_workflow_store

        init_workflow_store(
            yaml_path=settings.orchestrator_workflows_path,
            overrides_path=settings.orchestrator_overrides_path,
            extra_workflows_dir=settings.orchestrator_extra_workflows_dir,
        )
        logger.info(
            "orchestrator enabled workflows=%s extra=%s overrides=%s",
            settings.orchestrator_workflows_path,
            settings.orchestrator_extra_workflows_dir,
            settings.orchestrator_overrides_path,
        )
    # Phase H #38 — Multi-Agent 协作框架
    if settings.multi_agent_enabled:
        from packages.agent.multi_agent import init_agent_registry

        init_agent_registry(
            yaml_path=settings.agents_config_path,
            overrides_path=settings.agents_overrides_path,
        )
        logger.info(
            "multi_agent enabled yaml=%s overrides=%s",
            settings.agents_config_path,
            settings.agents_overrides_path,
        )
    # Phase H #39 — Agent 生命周期管理
    if settings.agent_lifecycle_enabled:
        from packages.agent.lifecycle import init_lifecycle_registry

        init_lifecycle_registry(
            yaml_path=settings.agent_lifecycle_versions_path,
            overrides_path=settings.agent_lifecycle_overrides_path,
        )
        logger.info(
            "agent_lifecycle enabled versions=%s overrides=%s",
            settings.agent_lifecycle_versions_path,
            settings.agent_lifecycle_overrides_path,
        )
    # Phase H #40 — HITL 完整工作流
    if settings.hitl_enabled:
        from packages.hitl import init_approval_store

        init_approval_store(database_url=settings.hitl_store_database_url)
        logger.info(
            "hitl enabled database_url=%s",
            "configured" if settings.hitl_store_database_url else "memory",
        )
    # Phase G #35 — Embedding 独立服务
    if settings.embedding_service_enabled:
        from packages.embedding import init_embedding_service

        init_embedding_service(
            registry_yaml_path=settings.embedding_models_config_path,
            registry_overrides_path=settings.embedding_models_overrides_path,
        )
        logger.info(
            "embedding_service enabled models=%s overrides=%s",
            settings.embedding_models_config_path,
            settings.embedding_models_overrides_path,
        )
    # Phase I #41 — 沙箱容器隔离
    if settings.sandbox_enabled:
        from packages.sandbox import init_sandbox_executor

        init_sandbox_executor(
            yaml_path=settings.sandbox_profiles_config_path,
            overrides_path=settings.sandbox_profiles_overrides_path,
        )
        logger.info("sandbox enabled runtime=%s", settings.sandbox_default_runtime)
    # Phase I #42 — 动作分级审计
    if settings.audit_actions_enabled:
        from packages.audit.action_levels import init_classifier
        from packages.audit.action_logger import init_action_logger

        init_classifier(
            yaml_path=settings.audit_actions_config_path,
            overrides_path=settings.audit_actions_overrides_path,
        )
        init_action_logger(database_url=settings.audit_actions_store_database_url)
        logger.info("audit_actions enabled")
    # Phase I #43 — PII 脱敏 + 内容安全
    if settings.pii_service_enabled:
        from packages.pii import init_pii_service

        init_pii_service(
            detector_yaml=settings.pii_patterns_config_path,
            detector_overrides=settings.pii_patterns_overrides_path,
            safety_yaml=settings.pii_safety_keywords_path,
        )
        logger.info("pii_service enabled")
    # Phase I #44 — OAuth2 / mTLS（opt-in，默认关闭）
    if settings.oauth2_enabled:
        from packages.auth.oauth2 import OAuth2Config, init_oauth2_provider

        init_oauth2_provider(
            OAuth2Config(
                client_id=settings.oauth2_client_id or "",
                client_secret=settings.oauth2_client_secret or "",
                authorization_endpoint=settings.oauth2_authorization_endpoint,
                token_endpoint=settings.oauth2_token_endpoint,
                userinfo_endpoint=settings.oauth2_userinfo_endpoint,
                redirect_uri=settings.oauth2_redirect_uri,
                scopes=settings.oauth2_scopes.split(),
                issuer=settings.oauth2_issuer or "",
            )
        )
        logger.info("oauth2 enabled issuer=%s", settings.oauth2_issuer)
    if settings.mtls_enabled:
        from packages.auth.mtls import MTLSConfig, init_mtls_context

        init_mtls_context(
            MTLSConfig(
                enabled=True,
                ca_cert_path=settings.mtls_ca_cert_path or "",
                server_cert_path=settings.mtls_server_cert_path or "",
                server_key_path=settings.mtls_server_key_path or "",
                client_cert_required=settings.mtls_client_cert_required,
            )
        )
        logger.info("mtls enabled")
    # Phase K #33 — 对象存储接入
    from packages.storage import StorageConfig, init_storage

    init_storage(
        StorageConfig(
            backend=settings.storage_backend,
            bucket=settings.storage_bucket,
            prefix=settings.storage_prefix,
            region=settings.storage_region,
            endpoint=settings.storage_endpoint,
            access_key=settings.storage_access_key,
            secret_key=settings.storage_secret_key,
            local_root=settings.storage_local_root,
        )
    )
    logger.info("storage backend=%s bucket=%s", settings.storage_backend, settings.storage_bucket)
    # Phase J #32 — 反馈飞轮
    if settings.feedback_enabled:
        from packages.feedback import init_feedback_store

        init_feedback_store(database_url=settings.feedback_store_database_url)
        logger.info("feedback enabled database_url=%s", "configured" if settings.feedback_store_database_url else "memory")
    if settings.quality_monitor_enabled:
        from packages.quality_monitor import init_quality_monitor

        init_quality_monitor()
        logger.info("quality_monitor enabled window=%ss", settings.quality_monitor_window_seconds)
    if settings.feedback_loop_enabled:
        from packages.feedback_loop import init_feedback_loop

        init_feedback_loop()
        logger.info("feedback_loop enabled")
    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.add_middleware(TraceIdMiddleware)
    app.include_router(rag_router)
    app.include_router(rag_query_router)
    app.include_router(agent_router)
    app.include_router(agent_approval_router)
    app.include_router(plan_workflow_router)
    app.include_router(plan_approval_router)
    app.include_router(audit_router)
    app.include_router(audit_action_router)
    app.include_router(auth_router)
    app.include_router(billing_router)
    app.include_router(embedding_router)
    app.include_router(hitl_router)
    app.include_router(mcp_router)
    app.include_router(memory_router)
    app.include_router(multi_agent_router)
    app.include_router(orchestrator_router)
    app.include_router(pii_router)
    app.include_router(platform_router)
    app.include_router(prompt_router)
    app.include_router(prompt_experiment_router)
    app.include_router(agent_lifecycle_router)
    app.include_router(sandbox_router)
    app.include_router(storage_router)
    app.include_router(feedback_router)
    app.include_router(quality_router)
    app.include_router(feedback_loop_router)
    app.include_router(console_router)
    app.include_router(console_rag_router)
    console_static = Path(__file__).resolve().parents[2] / "apps" / "console" / "static"
    if console_static.is_dir() and (console_static / "index.html").is_file():
        app.mount("/console", StaticFiles(directory=str(console_static), html=True), name="console")
    else:
        console_dir = Path(__file__).resolve().parents[2] / "apps" / "console"
        if console_dir.is_dir():
            app.mount("/console", StaticFiles(directory=str(console_dir), html=True), name="console")

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        start = time.perf_counter()
        trace_id = get_trace_id()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        tenant_id = request.headers.get("x-tenant-id")
        error_code = getattr(request.state, "audit_error_code", None)
        model = getattr(request.state, "audit_model", None)
        logger.info(
            "request",
            extra={
                "trace_id": trace_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": round(elapsed_ms, 2),
                "tenant_id": tenant_id,
            },
        )
        if settings.audit_enabled and request.url.path not in ("/healthz", "/metrics"):
            actor_role = getattr(request.state, "actor_role", None)
            try:
                from packages.audit.store import get_audit_store

                get_audit_store(settings.audit_db_path).insert(
                    tenant_id=tenant_id,
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    latency_ms=elapsed_ms,
                    trace_id=trace_id,
                    model=model,
                    error_code=error_code,
                )
            except Exception:
                logger.exception("audit insert failed path=%s", request.url.path)
            if settings.audit_postgres_enabled:
                try:
                    from packages.audit.postgres_store import AuditPostgresStore
                    from packages.billing.db import get_effective_database_url

                    pg_url = get_effective_database_url(settings.database_url)
                    if pg_url:
                        AuditPostgresStore(pg_url).insert(
                            tenant_id=tenant_id,
                            actor_role=actor_role,
                            method=request.method,
                            path=request.url.path,
                            status_code=response.status_code,
                            latency_ms=elapsed_ms,
                            trace_id=trace_id,
                            model=model,
                            error_code=error_code,
                        )
                except Exception:
                    logger.exception("audit postgres insert failed path=%s", request.url.path)
        return response

    @app.middleware("http")
    async def region_context(request: Request, call_next):
        from packages.region.context import clear_request_region
        from packages.region.middleware import bind_region_context

        region_err = await bind_region_context(request)
        if region_err is not None:
            return region_err
        try:
            return await call_next(request)
        finally:
            clear_request_region()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": settings.app_version}

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        if not settings.metrics_enabled:
            return PlainTextResponse("# metrics disabled\n", status_code=503)
        parts = [get_metrics_store().prometheus_text()]
        # Phase G — 语义缓存指标
        try:
            from packages.semantic_cache import get_semantic_cache_metrics

            parts.append(get_semantic_cache_metrics().prometheus_text())
        except Exception:
            logger.exception("semantic cache metrics export failed")
        # Phase F #31 — 长记忆指标
        try:
            from packages.memory import get_memory_metrics

            parts.append(get_memory_metrics().prometheus_text())
        except Exception:
            logger.exception("memory metrics export failed")
        try:
            from packages.rag.index_metrics import get_index_metrics

            parts.append(get_index_metrics().prometheus_text())
        except Exception:
            logger.exception("rag index metrics export failed")
        try:
            from packages.agent.perf_metrics import get_agent_perf_metrics

            parts.append(get_agent_perf_metrics().prometheus_text())
        except Exception:
            logger.exception("agent perf metrics export failed")
        return PlainTextResponse("".join(parts), media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        body: ChatCompletionRequest,
        x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        tenants = get_tenants()
        try:
            tenant = resolve_tenant(x_tenant_id, authorization, tenants)
        except HTTPException as e:
            return json_error(int(e.status_code), "UNAUTHORIZED", str(e.detail))

        request.state.actor_role = tenant.role

        if body.stream:
            return json_error(
                400,
                "BAD_REQUEST",
                "当前骨架暂不支持 stream=true，请使用非流式",
            )

        rate_err = check_rate_limit(tenant)
        if rate_err is not None:
            return rate_err

        budget_err = check_token_budget(tenant)
        if budget_err is not None:
            return budget_err

        model_err, resolved_model = check_model_allowed(tenant, body.model)
        if model_err is not None:
            return model_err

        if not quota_tracker.try_consume(tenant.tenant_id, tenant.daily_request_quota):
            return json_error(
                429,
                "QUOTA_EXCEEDED",
                "租户日配额已用尽（UTC 日切重置）",
                detail={"tenant_id": tenant.tenant_id, "quota": tenant.daily_request_quota},
            )

        if not (settings.llm_api_key or "").strip():
            return json_error(
                503,
                "UPSTREAM_NOT_CONFIGURED",
                "LLM_API_KEY 未配置：申请到账号后写入项目根目录 .env 即可联调",
            )

        from packages.observability.otel import component_span

        payload = body.upstream_payload(resolved_model)

        # Phase G — 语义缓存查询
        cache = get_semantic_cache()
        cache_lookup = None
        if cache is not None:
            cache_lookup = await cache.lookup(
                tenant_id=tenant.tenant_id,
                model=resolved_model,
                messages=[m.model_dump() for m in body.messages],
                temperature=body.temperature,
                stream=bool(body.stream),
            )
            if isinstance(cache_lookup, str):
                # 跳过缓存（如 stream=true / temperature 过高），继续走上游
                logger.debug("semantic cache skipped: %s", cache_lookup)
                cache_lookup = None
            elif cache_lookup is not None:
                # 命中缓存：返回带 _platform.cache_hit 标记
                cached_body = dict(cache_lookup.entry.response)
                meta = cached_body.setdefault("_platform", {})
                if isinstance(meta, dict):
                    meta["cache_hit"] = True
                    meta["cache_mode"] = cache_lookup.mode
                    meta["cache_similarity"] = round(cache_lookup.similarity, 4)
                    meta["cache_age_seconds"] = round(
                        time.time() - cache_lookup.entry.created_at, 2
                    )
                    meta["model"] = cache_lookup.entry.model
                    meta["tenant_id"] = tenant.tenant_id
                logger.info(
                    "semantic cache hit tenant=%s model=%s mode=%s sim=%.4f",
                    tenant.tenant_id,
                    resolved_model,
                    cache_lookup.mode,
                    cache_lookup.similarity,
                )
                return JSONResponse(status_code=200, content=cached_body)

        with component_span(
            "gateway.chat_completions",
            component="gateway",
            enabled=settings.otel_enabled,
            tenant_id=tenant.tenant_id,
            model=resolved_model,
        ):
            routed = await forward_with_model_router(
                payload,
                requested_model=body.model,
                tenant_default=tenant.default_model,
            )

        if routed.error and routed.body is None:
            code = "CIRCUIT_OPEN" if "熔断" in (routed.error or "") else "UPSTREAM_ERROR"
            return json_error(
                503,
                code,
                routed.error,
                detail={
                    "upstream_status": routed.status,
                    "models_tried": list(routed.models_tried),
                },
            )

        if routed.body is None:
            return json_error(502, "UPSTREAM_ERROR", "empty upstream body")

        if not (200 <= routed.status < 300):
            return json_error(
                routed.status if 400 <= routed.status < 600 else 502,
                "UPSTREAM_ERROR",
                f"upstream status {routed.status}",
                detail={
                    "upstream": routed.body,
                    "models_tried": list(routed.models_tried),
                },
            )

        content = dict(routed.body)
        usage = record_upstream_usage(
            tenant_id=tenant.tenant_id,
            path="/v1/chat/completions",
            model=routed.model_used or resolved_model,
            upstream_body=routed.body,
            trace_id=get_trace_id(),
        )
        snap = get_budget_snapshot(
            tenant.tenant_id,
            token_budget_daily=tenant.token_budget_daily,
            token_budget_monthly=tenant.token_budget_monthly,
        )
        meta = content.setdefault("_platform", {})
        if isinstance(meta, dict):
            if routed.fallback_used and routed.model_used:
                meta["model_used"] = routed.model_used
                meta["fallback_used"] = True
                meta["models_tried"] = list(routed.models_tried)
            if routed.provider_id:
                meta["provider_id"] = routed.provider_id
            if usage is not None:
                meta["usage"] = {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "total_tokens": usage.total_tokens,
                    **budget_platform_meta(snap, usage.total_tokens),
                }

        # Phase G — 写入语义缓存（仅成功响应且 cache 未跳过时）
        if cache is not None and cache_lookup is None:
            try:
                await cache.store(
                    tenant_id=tenant.tenant_id,
                    model=resolved_model,
                    messages=[m.model_dump() for m in body.messages],
                    response=content,
                    usage_tokens=(usage.total_tokens if usage else 0),
                    temperature=body.temperature,
                    stream=bool(body.stream),
                )
            except Exception:
                logger.exception("semantic cache store failed")

        return JSONResponse(status_code=200, content=content)

    return app


app = create_app()
