"""Gateway 依赖装配 — Phase init 顺序（Issue #156 PR-1）。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from packages.agent.registry import build_default_registry
from packages.mcp import init_mcp_registry
from packages.memory import init_memory_store
from packages.observability.otel import init_otel
from packages.prompt import init_experiment_store as init_prompt_experiment_store
from packages.prompt import init_registry as init_prompt_registry
from packages.semantic_cache import SemanticCacheConfig, init_semantic_cache

if TYPE_CHECKING:
    from apps.gateway.settings import Settings

logger = logging.getLogger("ai_platform.gateway.composition")


def build_semantic_cache_config(settings: Settings) -> SemanticCacheConfig:
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


def wire_gateway_dependencies(settings: Settings) -> None:
    """按 Phase 顺序初始化 gateway 依赖的全局单例 / store。"""
    init_otel(
        service_name=settings.app_name,
        enabled=settings.otel_enabled,
        console_export=settings.otel_console_export,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
    )
    if settings.semantic_cache_enabled:
        init_semantic_cache(
            build_semantic_cache_config(settings),
            redis_url=settings.redis_url or None,
        )
        logger.info(
            "semantic cache enabled mode=%s threshold=%.2f ttl=%ds",
            settings.semantic_cache_mode,
            settings.semantic_cache_similarity_threshold,
            settings.semantic_cache_ttl_seconds,
        )
    if settings.prompt_registry_enabled:
        init_prompt_registry(
            yaml_path=settings.prompts_config_path,
            overrides_path=settings.prompt_overrides_path,
            legacy_fallback={"rag_query": settings.rag_prompt_path},
        )
        logger.info(
            "prompt registry enabled yaml=%s overrides=%s",
            settings.prompts_config_path,
            settings.prompt_overrides_path,
        )
        if settings.prompt_experiment_enabled:
            init_prompt_experiment_store(storage_path=settings.prompt_experiments_path)
            logger.info("prompt experiment enabled storage=%s", settings.prompt_experiments_path)
    if settings.memory_store_enabled:
        init_memory_store(database_url=settings.database_url or None)
        logger.info(
            "memory store enabled database_url=%s",
            "configured" if settings.database_url else "memory-fallback",
        )
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
    if settings.agent_plugins_enabled:
        from packages.agent.plugins.loader import get_loaded_plugins

        reserved = frozenset(build_default_registry().keys())
        plugins = get_loaded_plugins(reserved_names=reserved)
        logger.info(
            "agent plugins enabled dir=%s count=%d",
            settings.agent_plugins_config_dir,
            len(plugins),
        )
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
    if settings.hitl_enabled:
        from packages.hitl import init_approval_store

        init_approval_store(database_url=settings.hitl_store_database_url)
        logger.info(
            "hitl enabled database_url=%s",
            "configured" if settings.hitl_store_database_url else "memory",
        )
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
    if settings.sandbox_enabled:
        from packages.sandbox import init_sandbox_executor

        init_sandbox_executor(
            yaml_path=settings.sandbox_profiles_config_path,
            overrides_path=settings.sandbox_profiles_overrides_path,
        )
        logger.info("sandbox enabled runtime=%s", settings.sandbox_default_runtime)
    if settings.audit_actions_enabled:
        from packages.audit.action_levels import init_classifier
        from packages.audit.action_logger import init_action_logger

        init_classifier(
            yaml_path=settings.audit_actions_config_path,
            overrides_path=settings.audit_actions_overrides_path,
        )
        init_action_logger(database_url=settings.audit_actions_store_database_url)
        logger.info("audit_actions enabled")
    if settings.pii_service_enabled:
        from packages.pii import init_pii_service

        init_pii_service(
            detector_yaml=settings.pii_patterns_config_path,
            detector_overrides=settings.pii_patterns_overrides_path,
            safety_yaml=settings.pii_safety_keywords_path,
        )
        logger.info("pii_service enabled")
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
    if settings.feedback_enabled:
        from packages.feedback import init_feedback_store

        init_feedback_store(database_url=settings.feedback_store_database_url)
        logger.info(
            "feedback enabled database_url=%s",
            "configured" if settings.feedback_store_database_url else "memory",
        )
    if settings.quality_monitor_enabled:
        from packages.quality_monitor import init_quality_monitor

        init_quality_monitor()
        logger.info("quality_monitor enabled window=%ss", settings.quality_monitor_window_seconds)
    if settings.feedback_loop_enabled:
        from packages.feedback_loop import init_feedback_loop

        init_feedback_loop()
        logger.info("feedback_loop enabled")
