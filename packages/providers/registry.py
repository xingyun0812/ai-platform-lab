from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Load .env into os.environ so api_key_env lookups work for any env var name
_dotenv_loaded = False


def _ensure_dotenv():
    global _dotenv_loaded
    if not _dotenv_loaded:
        _dotenv_loaded = True
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.is_file():
            load_dotenv(dotenv_path=env_path)


from packages.platform import REPO_ROOT, get_settings


@dataclass(frozen=True)
class ModelOffering:
    provider_id: str
    model: str
    base_url: str
    api_key: str
    input_price_per_1k: float
    output_price_per_1k: float
    latency_p50_ms: int
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class ProviderMatrix:
    routing_policy: str
    offerings: tuple[ModelOffering, ...]


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _resolve_api_key(key_env: str) -> str:
    """Resolve API key from environment (includes .env via dotenv)."""
    _ensure_dotenv()
    api_key = os.environ.get(key_env, "") if key_env else ""
    if not api_key and key_env == "LLM_API_KEY":
        api_key = (get_settings().llm_api_key or "").strip()
    return api_key


@lru_cache
def get_provider_matrix() -> ProviderMatrix:
    path = REPO_ROOT / "config" / "providers.yaml"
    raw = _read_yaml(path)
    policy = str(raw.get("routing_policy", "balanced"))
    providers_raw = raw.get("providers") or {}
    offerings: list[ModelOffering] = []
    if isinstance(providers_raw, dict):
        for provider_id, cfg in providers_raw.items():
            if not isinstance(cfg, dict):
                continue
            base_url = str(cfg.get("base_url", "")).rstrip("/")
            key_env = str(cfg.get("api_key_env", "LLM_API_KEY"))
            api_key = _resolve_api_key(key_env)
            models = cfg.get("models") or {}
            if not isinstance(models, dict):
                continue
            for model_name, meta in models.items():
                if not isinstance(meta, dict):
                    continue
                caps = meta.get("capabilities") or []
                offerings.append(
                    ModelOffering(
                        provider_id=str(provider_id),
                        model=str(model_name),
                        base_url=base_url,
                        api_key=api_key,
                        input_price_per_1k=float(meta.get("input_price_per_1k", 0)),
                        output_price_per_1k=float(meta.get("output_price_per_1k", 0)),
                        latency_p50_ms=int(meta.get("latency_p50_ms", 1000)),
                        capabilities=tuple(str(c) for c in caps) if isinstance(caps, list) else (),
                    )
                )
    return ProviderMatrix(routing_policy=policy, offerings=tuple(offerings))


def _score(offering: ModelOffering, policy: str) -> float:
    cost = offering.input_price_per_1k + offering.output_price_per_1k
    latency = float(offering.latency_p50_ms)
    if policy == "cost":
        return -cost
    if policy == "latency":
        return -latency
    return -(cost * 10.0 + latency * 0.01)


def pick_provider_for_model(model: str) -> ModelOffering | None:
    matrix = get_provider_matrix()
    candidates = [o for o in matrix.offerings if o.model == model and o.base_url]
    if not candidates:
        settings = get_settings()
        key = (settings.llm_api_key or "").strip()
        if not key:
            return None
        return ModelOffering(
            provider_id="default",
            model=model,
            base_url=settings.llm_base_url.rstrip("/"),
            api_key=key,
            input_price_per_1k=0,
            output_price_per_1k=0,
            latency_p50_ms=1000,
            capabilities=(),
        )
    with_key = [c for c in candidates if c.api_key]
    pool = with_key or candidates
    best = max(pool, key=lambda o: _score(o, matrix.routing_policy))
    return best


def matrix_payload() -> dict[str, Any]:
    matrix = get_provider_matrix()
    rows = [
        {
            "provider_id": o.provider_id,
            "model": o.model,
            "base_url": o.base_url,
            "input_price_per_1k": o.input_price_per_1k,
            "output_price_per_1k": o.output_price_per_1k,
            "latency_p50_ms": o.latency_p50_ms,
            "capabilities": list(o.capabilities),
            "configured": bool(o.api_key),
        }
        for o in matrix.offerings
    ]
    return {"routing_policy": matrix.routing_policy, "offerings": rows}
