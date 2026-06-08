from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from apps.gateway.llm_proxy import forward_chat_completions
from apps.gateway.settings import get_settings

logger = logging.getLogger("ai_platform.gateway.model_router")

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class ModelRouterConfig:
    aliases: dict[str, str]
    fallback_chains: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class ModelRouteResult:
    status: int
    body: dict[str, Any] | None
    error: str | None
    model_used: str | None
    models_tried: tuple[str, ...]
    fallback_used: bool
    provider_id: str | None = None


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


@lru_cache
def get_model_router_config() -> ModelRouterConfig:
    settings = get_settings()
    raw = _read_yaml(settings.models_config_path)
    aliases_raw = raw.get("aliases") or {}
    chains_raw = raw.get("fallback_chains") or {}
    aliases = {str(k): str(v) for k, v in aliases_raw.items()} if isinstance(aliases_raw, dict) else {}
    chains: dict[str, tuple[str, ...]] = {}
    if isinstance(chains_raw, dict):
        for key, value in chains_raw.items():
            if isinstance(value, list):
                chains[str(key)] = tuple(str(m) for m in value)
    return ModelRouterConfig(aliases=aliases, fallback_chains=chains)


def resolve_model_name(
    requested: str | None,
    *,
    tenant_default: str | None = None,
) -> str:
    """将别名 / 租户默认 / 全局默认解析为上游模型名。"""
    settings = get_settings()
    cfg = get_model_router_config()
    raw = requested or tenant_default or settings.default_model
    return cfg.aliases.get(raw, raw)


def is_model_allowed(
    requested: str | None,
    *,
    tenant_default: str | None,
    allowed_models: tuple[str, ...],
) -> tuple[bool, str]:
    """白名单校验：允许别名或其解析后的真实模型名。"""
    if not allowed_models:
        return True, resolve_model_name(requested, tenant_default=tenant_default)
    raw = requested or tenant_default or get_settings().default_model
    resolved = resolve_model_name(requested, tenant_default=tenant_default)
    if raw in allowed_models or resolved in allowed_models:
        return True, resolved
    return False, resolved


def _fallback_chain(primary: str) -> tuple[str, ...]:
    cfg = get_model_router_config()
    chain = cfg.fallback_chains.get(primary) or cfg.fallback_chains.get("default") or (primary,)
    if primary not in chain:
        return (primary, *chain)
    return chain


def _should_try_fallback(status: int, err: str | None) -> bool:
    if err:
        return True
    return status in _RETRYABLE_STATUSES


async def forward_with_model_router(
    payload: dict[str, Any],
    *,
    requested_model: str | None = None,
    tenant_default: str | None = None,
) -> ModelRouteResult:
    """按降级链调用上游；成功时 meta 标明实际 model。"""
    primary = resolve_model_name(requested_model, tenant_default=tenant_default)
    chain = _fallback_chain(primary)
    tried: list[str] = []
    last_status = 503
    last_body: dict[str, Any] | None = None
    last_err: str | None = None

    for idx, model_name in enumerate(chain):
        if model_name in tried:
            continue
        tried.append(model_name)
        attempt_payload = {**payload, "model": model_name}
        from packages.providers.registry import pick_provider_for_model

        offering = pick_provider_for_model(model_name)
        status, body, err = await forward_chat_completions(
            attempt_payload,
            base_url=offering.base_url if offering else None,
            api_key=offering.api_key if offering else None,
        )
        last_status, last_body, last_err = status, body, err

        if err is None and body is not None and 200 <= status < 300:
            return ModelRouteResult(
                status=status,
                body=body,
                error=None,
                model_used=model_name,
                models_tried=tuple(tried),
                fallback_used=idx > 0,
                provider_id=offering.provider_id if offering else None,
            )

        if not _should_try_fallback(status, err) or idx >= len(chain) - 1:
            break
        logger.warning(
            "model fallback tenant_model=%s try=%s status=%s err=%s",
            primary,
            model_name,
            status,
            err,
        )

    return ModelRouteResult(
        status=last_status,
        body=last_body,
        error=last_err,
        model_used=None,
        models_tried=tuple(tried),
        fallback_used=len(tried) > 1,
        provider_id=None,
    )
