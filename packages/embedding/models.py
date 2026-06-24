"""Embedding 数据模型 + 注册表 — Issue #35

存储：
    config/embedding_models.yaml — git 跟踪的默认配置
    data/embedding_models_overrides.json — admin API 运行时修改（不进 git）
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("ai_platform.embedding.models")


@dataclass
class EmbeddingModel:
    """Embedding 模型配置。"""

    model_id: str
    name: str
    provider: str  # "openai" | "stub" | "custom"
    dimensions: int
    max_input_tokens: int = 8192
    modalities: list[str] = field(default_factory=lambda: ["text"])
    created_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EmbeddingRequest:
    """Embedding 请求。"""

    model_id: str
    texts: list = field(default_factory=list)
    inputs: list | None = None
    tenant_id: str = "system"

    def resolved_items(self) -> list[dict]:
        from packages.embedding.multimodal import normalize_item, normalize_items

        if self.inputs is not None:
            return normalize_items(self.inputs)
        if not self.texts:
            raise ValueError("texts or inputs required")
        return [normalize_item(t) for t in self.texts]


@dataclass
class EmbeddingResponse:
    """Embedding 响应。"""

    model_id: str
    embeddings: list
    dimensions: int
    usage: dict
    cached: bool = False


class EmbeddingRegistry:
    """Embedding 模型注册表。

    线程安全。启动时从 YAML + JSON overrides 加载。
    """

    def __init__(
        self,
        *,
        yaml_path: Path | None = None,
        overrides_path: Path | None = None,
    ) -> None:
        self._yaml_path = yaml_path
        self._overrides_path = overrides_path
        self._lock = threading.RLock()
        self._models: dict[str, EmbeddingModel] = {}
        self._loaded = False

    def load(self) -> None:
        with self._lock:
            self._models.clear()
            if self._yaml_path and self._yaml_path.is_file():
                try:
                    data = yaml.safe_load(self._yaml_path.read_text(encoding="utf-8"))
                    self._merge_yaml(data)
                    logger.info(
                        "embedding registry loaded yaml=%s models=%d",
                        self._yaml_path,
                        len(self._models),
                    )
                except Exception as e:
                    logger.warning("embedding yaml load failed: %s", e)
            if self._overrides_path and self._overrides_path.is_file():
                try:
                    data = json.loads(self._overrides_path.read_text(encoding="utf-8"))
                    self._merge_overrides(data)
                    logger.info(
                        "embedding registry loaded overrides=%s models=%d",
                        self._overrides_path,
                        len(self._models),
                    )
                except Exception as e:
                    logger.warning("embedding overrides load failed: %s", e)
            self._loaded = True

    def _merge_yaml(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        models = data.get("models")
        if not isinstance(models, list):
            return
        for item in models:
            model = self._parse_model(item)
            if model is not None:
                self._models[model.model_id] = model

    def _merge_overrides(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        models = data.get("models")
        if not isinstance(models, list):
            return
        for item in models:
            model = self._parse_model(item)
            if model is not None:
                self._models[model.model_id] = model

    def _parse_model(self, item: Any) -> EmbeddingModel | None:
        if not isinstance(item, dict):
            return None
        try:
            model_id = str(item["model_id"])
            from packages.embedding.multimodal import parse_modalities

            return EmbeddingModel(
                model_id=model_id,
                name=str(item.get("name", model_id)),
                provider=str(item.get("provider", "stub")),
                dimensions=int(item.get("dimensions", 1536)),
                max_input_tokens=int(item.get("max_input_tokens", 8192)),
                modalities=parse_modalities(item.get("modalities")),
                created_at=float(item.get("created_at", time.time())),
                metadata=dict(item.get("metadata", {})),
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("embedding model parse failed: %s item=%r", e, item)
            return None

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def register_model(self, model: EmbeddingModel) -> EmbeddingModel:
        self._ensure_loaded()
        with self._lock:
            self._models[model.model_id] = model
            self._persist()
            return model

    def get_model(self, model_id: str) -> EmbeddingModel | None:
        self._ensure_loaded()
        with self._lock:
            return self._models.get(model_id)

    def list_models(self) -> list:
        self._ensure_loaded()
        with self._lock:
            return [self._models[mid] for mid in sorted(self._models.keys())]

    def remove_model(self, model_id: str) -> bool:
        self._ensure_loaded()
        with self._lock:
            if model_id not in self._models:
                return False
            del self._models[model_id]
            self._persist()
            return True

    def _persist(self) -> None:
        if not self._overrides_path:
            return
        try:
            self._overrides_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "models": [m.to_dict() for m in self._models.values()]
            }
            self._overrides_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error("embedding persist failed: %s", e)

    def stats(self) -> dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            total = len(self._models)
            by_provider: dict[str, int] = {}
            for m in self._models.values():
                by_provider[m.provider] = by_provider.get(m.provider, 0) + 1
            return {
                "total_models": total,
                "by_provider": by_provider,
            }


# --------------------------------------------------------------------- #
# 全局单例
# --------------------------------------------------------------------- #

_global_registry: EmbeddingRegistry | None = None
_global_lock = threading.Lock()


def init_registry(
    *,
    yaml_path: Path | None = None,
    overrides_path: Path | None = None,
) -> EmbeddingRegistry:
    global _global_registry
    with _global_lock:
        _global_registry = EmbeddingRegistry(
            yaml_path=yaml_path,
            overrides_path=overrides_path,
        )
        _global_registry.load()
        return _global_registry


def get_registry() -> EmbeddingRegistry | None:
    return _global_registry


def reset_for_tests() -> None:
    global _global_registry
    with _global_lock:
        _global_registry = None
