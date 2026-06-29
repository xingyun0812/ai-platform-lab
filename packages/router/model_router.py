from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from packages.llm.chat import forward_chat_completions
from packages.platform import get_settings

logger = logging.getLogger("ai_platform.router.model_router")

_KNOWN_MODELS = (
    "chat-fast",
    "chat-smart",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-3.5-turbo",
    "claude-3-5-sonnet",
    "claude-3-haiku",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
)

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def select_model_with_capability(
    prompt_type: str = "default",
    required_dimension: str | None = None,
) -> str:
    """根据 prompt 类型 + 所需维度选模型。"""
    default_model = get_settings().default_model

    if required_dimension is None:
        logger.debug(
            "select_model_with_capability no dimension required, using default=%s",
            default_model,
        )
        return default_model

    try:
        from packages.agent.capability_profile import (
            dim_to_field,
            get_capability_profile_store,
        )

        store = get_capability_profile_store()
        field_name = dim_to_field(required_dimension)
        if field_name is None:
            logger.warning(
                "select_model_with_capability unknown dimension=%s, using default",
                required_dimension,
            )
            return default_model

        candidates: list[tuple[str, float]] = []
        for model_id in _KNOWN_MODELS:
            profile = store.get_latest(model_id)
            if profile is None:
                continue
            score = getattr(profile.scores, field_name, 0.0)
            candidates.append((model_id, score))

        if not candidates:
            logger.debug(
                "select_model_with_capability no profiles found for dimension=%s, using default=%s",
                required_dimension,
                default_model,
            )
            return default_model

        candidates.sort(key=lambda x: x[1], reverse=True)
        selected = candidates[0][0]
        logger.info(
            "select_model_with_capability dimension=%s selected=%s score=%.3f prompt_type=%s",
            required_dimension,
            selected,
            candidates[0][1],
            prompt_type,
        )
        return selected
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "select_model_with_capability error=%s, falling back to default=%s",
            exc,
            default_model,
        )
        return default_model


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
    aliases = (
        {str(k): str(v) for k, v in aliases_raw.items()} if isinstance(aliases_raw, dict) else {}
    )
    chains: dict[str, tuple[str, ...]] = {}
    if isinstance(chains_raw, dict):
        for key, value in chains_raw.items():
            if isinstance(value, list):
                chains[str(key)] = tuple(str(m) for m in value)
    return ModelRouterConfig(aliases=aliases, fallback_chains=chains)


def reset_model_router_config_for_tests() -> None:
    get_model_router_config.cache_clear()


def resolve_model_name(
    requested: str | None,
    *,
    tenant_default: str | None = None,
) -> str:
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
    required_capability: str | None = payload.get("required_capability")
    if required_capability:
        cap_model = select_model_with_capability(
            prompt_type=payload.get("prompt_type", "default"),
            required_dimension=required_capability,
        )
        if cap_model and cap_model != get_settings().default_model:
            requested_model = cap_model
            logger.info(
                "R3 capability routing: required_capability=%s → model=%s",
                required_capability,
                cap_model,
            )
        elif cap_model:
            requested_model = requested_model or cap_model

    primary = resolve_model_name(requested_model, tenant_default=tenant_default)
    chain = _fallback_chain(primary)
    tried: list[str] = []
    last_status = 503
    last_body: dict[str, Any] | None = None
    last_err: str | None = None

    from packages.router.circuit_breaker import get_circuit_breaker

    breaker = get_circuit_breaker()
    breaker.failure_threshold = max(1, get_settings().circuit_breaker_threshold)

    for idx, model_name in enumerate(chain):
        if model_name in tried:
            continue
        tried.append(model_name)
        allowed, _cb_state = breaker.allow(model_name)
        if not allowed:
            return ModelRouteResult(
                status=503,
                body=None,
                error=f"熔断已打开 model={model_name}",
                model_used=None,
                models_tried=tuple(tried),
                fallback_used=idx > 0,
                provider_id=None,
            )
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
            breaker.record_success(model_name)
            return ModelRouteResult(
                status=status,
                body=body,
                error=None,
                model_used=model_name,
                models_tried=tuple(tried),
                fallback_used=idx > 0,
                provider_id=offering.provider_id if offering else None,
            )

        if _should_try_fallback(status, err):
            breaker.record_failure(model_name)
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
