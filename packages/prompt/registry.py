"""Prompt 版本注册表 — Phase F #29

数据模型：
    PromptVersion
        prompt_id:    str          # "rag_query", "agent_system"
        version:       int          # 1, 2, 3...
        content:       str          # 模板内容，支持 {{var}}
        variables:     list[str]    # 自动从 content 解析
        status:        str          # "draft" | "active" | "archived"
        tenant_id:     str          # "global" 或具体租户（覆盖）
        changelog:     str          # 版本变更说明
        created_at:    float
        created_by:    str

存储：
    1. YAML 默认值：config/prompts.yaml（git 跟踪，初始版本）
    2. JSON overrides：data/prompt_overrides.json（运行时修改，admin 写入）

启动时合并：YAML + JSON overrides。写入仅修改 JSON。

向后兼容：
    若 prompt_id 在 registry 中不存在，可回退到 legacy txt 文件。
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

from packages.prompt.render import extract_variables, render

logger = logging.getLogger("ai_platform.prompt")


@dataclass
class PromptVersion:
    prompt_id: str
    version: int
    content: str
    variables: list[str] = field(default_factory=list)
    status: str = "draft"  # draft | active | archived
    tenant_id: str = "global"
    changelog: str = ""
    created_at: float = field(default_factory=time.time)
    created_by: str = "system"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def render(self, variables: dict[str, Any] | None = None) -> str:
        return render(self.content, variables)


class PromptRegistryError(Exception):
    """Prompt registry 业务错误。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class PromptRegistry:
    """Prompt 版本注册表。

    线程安全。启动时从 YAML 加载默认，从 JSON 加载 overrides，合并后缓存。
    写入操作（create_version / set_active）落 JSON 文件。
    """

    def __init__(
        self,
        *,
        yaml_path: Path | None = None,
        overrides_path: Path | None = None,
        legacy_fallback: dict[str, Path] | None = None,
    ) -> None:
        self._yaml_path = yaml_path
        self._overrides_path = overrides_path
        self._legacy_fallback = legacy_fallback or {}
        self._lock = threading.RLock()
        # _versions[(tenant_id, prompt_id)] = {version: PromptVersion}
        self._versions: dict[tuple[str, str], dict[int, PromptVersion]] = {}
        self._loaded = False

    # ------------------------------------------------------------------ #
    # 加载
    # ------------------------------------------------------------------ #

    def load(self) -> None:
        """加载 YAML 默认 + JSON overrides。"""
        with self._lock:
            self._versions.clear()
            if self._yaml_path and self._yaml_path.is_file():
                try:
                    data = yaml.safe_load(self._yaml_path.read_text(encoding="utf-8"))
                    self._merge_yaml(data)
                    logger.info(
                        "prompt registry loaded yaml=%s entries=%d",
                        self._yaml_path,
                        sum(len(v) for v in self._versions.values()),
                    )
                except Exception as e:
                    logger.warning("prompt yaml load failed: %s", e)
            if self._overrides_path and self._overrides_path.is_file():
                try:
                    data = json.loads(self._overrides_path.read_text(encoding="utf-8"))
                    self._merge_overrides(data)
                    logger.info(
                        "prompt registry loaded overrides=%s entries=%d",
                        self._overrides_path,
                        sum(len(v) for v in self._versions.values()),
                    )
                except Exception as e:
                    logger.warning("prompt overrides load failed: %s", e)
            self._loaded = True

    def _merge_yaml(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        prompts = data.get("prompts")
        if not isinstance(prompts, list):
            return
        for item in prompts:
            if not isinstance(item, dict):
                continue
            entry = self._parse_entry(item)
            if entry is not None:
                self._versions.setdefault(
                    (entry.tenant_id, entry.prompt_id), {}
                )[entry.version] = entry

    def _merge_overrides(self, data: Any) -> None:
        """JSON overrides 覆盖 YAML；同 (tenant_id, prompt_id, version) 替换。"""
        if not isinstance(data, dict):
            return
        prompts = data.get("prompts")
        if not isinstance(prompts, list):
            return
        for item in prompts:
            if not isinstance(item, dict):
                continue
            entry = self._parse_entry(item)
            if entry is not None:
                self._versions.setdefault(
                    (entry.tenant_id, entry.prompt_id), {}
                )[entry.version] = entry

    def _parse_entry(self, item: dict[str, Any]) -> PromptVersion | None:
        try:
            prompt_id = str(item["prompt_id"])
            version = int(item["version"])
            content = str(item["content"])
            variables = item.get("variables") or extract_variables(content)
            status = str(item.get("status", "draft"))
            tenant_id = str(item.get("tenant_id", "global"))
            changelog = str(item.get("changelog", ""))
            created_at = float(item.get("created_at", time.time()))
            created_by = str(item.get("created_by", "system"))
            return PromptVersion(
                prompt_id=prompt_id,
                version=version,
                content=content,
                variables=list(variables),
                status=status,
                tenant_id=tenant_id,
                changelog=changelog,
                created_at=created_at,
                created_by=created_by,
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("prompt entry parse failed: %s item=%r", e, item)
            return None

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #

    def list_prompt_ids(self, *, tenant_id: str = "global") -> list[str]:
        self._ensure_loaded()
        with self._lock:
            return sorted(
                {
                    pid
                    for (tid, pid) in self._versions.keys()
                    if tid == tenant_id
                }
            )

    def list_versions(
        self, prompt_id: str, *, tenant_id: str = "global"
    ) -> list[PromptVersion]:
        self._ensure_loaded()
        with self._lock:
            bucket = self._versions.get((tenant_id, prompt_id), {})
            return sorted(bucket.values(), key=lambda v: v.version)

    def get_version(
        self,
        prompt_id: str,
        version: int,
        *,
        tenant_id: str = "global",
    ) -> PromptVersion | None:
        self._ensure_loaded()
        with self._lock:
            return self._versions.get((tenant_id, prompt_id), {}).get(version)

    def get_active(
        self, prompt_id: str, *, tenant_id: str = "global"
    ) -> PromptVersion | None:
        """获取 active 版本；若无 active 则返回最新 version（非 draft）。"""
        self._ensure_loaded()
        with self._lock:
            bucket = self._versions.get((tenant_id, prompt_id), {})
            if not bucket:
                return self._legacy_fallback_get(prompt_id)
            # 优先 active
            for v in bucket.values():
                if v.status == "active":
                    return v
            # 其次最新 archived/draft（按 version desc）
            for v in sorted(bucket.values(), key=lambda x: x.version, reverse=True):
                if v.status != "draft":
                    return v
            return None

    def _legacy_fallback_get(self, prompt_id: str) -> PromptVersion | None:
        """向后兼容：若 prompt_id 在 registry 中不存在，回退到 legacy txt 文件。"""
        path = self._legacy_fallback.get(prompt_id)
        if not path or not path.is_file():
            return None
        try:
            content = path.read_text(encoding="utf-8")
            return PromptVersion(
                prompt_id=prompt_id,
                version=0,  # 0 表示 legacy
                content=content,
                variables=extract_variables(content),
                status="active",
                tenant_id="global",
                changelog="legacy txt fallback",
                created_at=0.0,
                created_by="legacy",
            )
        except Exception as e:
            logger.warning("legacy fallback failed for %s: %s", prompt_id, e)
            return None

    def render(
        self,
        prompt_id: str,
        variables: dict[str, Any] | None = None,
        *,
        tenant_id: str = "global",
    ) -> tuple[str, PromptVersion | None]:
        """渲染 active prompt；返回 (渲染后文本, 使用的版本)。"""
        entry = self.get_active(prompt_id, tenant_id=tenant_id)
        if entry is None:
            raise PromptRegistryError(
                "PROMPT_NOT_FOUND",
                f"prompt_id={prompt_id} tenant_id={tenant_id} 未找到 active 版本",
            )
        return entry.render(variables), entry

    def render_with_experiment(
        self,
        prompt_id: str,
        variables: dict[str, Any] | None = None,
        *,
        tenant_id: str = "global",
        bucket_key: str,
        experiment_store: Any = None,
    ) -> tuple[str, PromptVersion | None, dict[str, Any]]:
        """Phase F #30：若存在运行中 A/B 实验，按 bucket_key 分桶取版本；
        否则回退到 active 版本。

        返回 (渲染后文本, 使用的版本, 实验信息)。
        实验信息：{"experiment_id": str|None, "variant_version": int|None}
        """
        exp_info: dict[str, Any] = {
            "experiment_id": None,
            "variant_version": None,
        }
        if experiment_store is not None:
            picked = experiment_store.pick_variant(
                prompt_id=prompt_id,
                tenant_id=tenant_id,
                bucket_key=bucket_key,
            )
            if picked is not None:
                exp, variant = picked
                entry = self.get_version(prompt_id, variant.version, tenant_id=tenant_id)
                if entry is not None:
                    exp_info["experiment_id"] = exp.experiment_id
                    exp_info["variant_version"] = variant.version
                    return entry.render(variables), entry, exp_info
        # 回退到 active
        rendered, entry = self.render(prompt_id, variables, tenant_id=tenant_id)
        return rendered, entry, exp_info

    # ------------------------------------------------------------------ #
    # 写入（落 JSON overrides）
    # ------------------------------------------------------------------ #

    def create_version(
        self,
        *,
        prompt_id: str,
        content: str,
        changelog: str = "",
        created_by: str = "admin",
        tenant_id: str = "global",
        set_active: bool = True,
    ) -> PromptVersion:
        """创建新版本；默认设为 active（archived 旧 active）。"""
        self._ensure_loaded()
        with self._lock:
            bucket = self._versions.setdefault((tenant_id, prompt_id), {})
            next_version = max([v.version for v in bucket.values()], default=0) + 1
            entry = PromptVersion(
                prompt_id=prompt_id,
                version=next_version,
                content=content,
                variables=extract_variables(content),
                status="active" if set_active else "draft",
                tenant_id=tenant_id,
                changelog=changelog,
                created_at=time.time(),
                created_by=created_by,
            )
            if set_active:
                # 旧 active → archived
                for v in bucket.values():
                    if v.status == "active":
                        v.status = "archived"
            bucket[next_version] = entry
            self._persist()
            logger.info(
                "prompt version created id=%s v=%d active=%s by=%s",
                prompt_id,
                next_version,
                set_active,
                created_by,
            )
            return entry

    def set_active(
        self,
        prompt_id: str,
        version: int,
        *,
        tenant_id: str = "global",
    ) -> PromptVersion:
        """切换 active 版本；旧 active → archived。"""
        self._ensure_loaded()
        with self._lock:
            bucket = self._versions.get((tenant_id, prompt_id), {})
            if version not in bucket:
                raise PromptRegistryError(
                    "VERSION_NOT_FOUND",
                    f"prompt_id={prompt_id} version={version} 不存在",
                )
            for v in bucket.values():
                if v.status == "active":
                    v.status = "archived"
            bucket[version].status = "active"
            self._persist()
            logger.info(
                "prompt active switched id=%s v=%d", prompt_id, version
            )
            return bucket[version]

    def archive_version(
        self,
        prompt_id: str,
        version: int,
        *,
        tenant_id: str = "global",
    ) -> PromptVersion:
        """归档版本（不可再激活，但可查询历史）。"""
        self._ensure_loaded()
        with self._lock:
            bucket = self._versions.get((tenant_id, prompt_id), {})
            if version not in bucket:
                raise PromptRegistryError(
                    "VERSION_NOT_FOUND",
                    f"prompt_id={prompt_id} version={version} 不存在",
                )
            bucket[version].status = "archived"
            self._persist()
            return bucket[version]

    def _persist(self) -> None:
        """将所有版本落 JSON overrides 文件。"""
        if not self._overrides_path:
            return
        try:
            self._overrides_path.parent.mkdir(parents=True, exist_ok=True)
            prompts: list[dict[str, Any]] = []
            for (tenant_id, prompt_id), bucket in self._versions.items():
                # 仅持久化非 yaml 来源的（version >= 1 且 created_by != "system" 的优先）
                # 简化策略：全部持久化，加载时覆盖即可
                for v in bucket.values():
                    prompts.append(v.to_dict())
            data = {"prompts": prompts}
            self._overrides_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error("prompt overrides persist failed: %s", e)
            raise

    # ------------------------------------------------------------------ #
    # 调试
    # ------------------------------------------------------------------ #

    def stats(self) -> dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            total = sum(len(v) for v in self._versions.values())
            active = sum(
                1
                for bucket in self._versions.values()
                for v in bucket.values()
                if v.status == "active"
            )
            return {
                "total_versions": total,
                "active_versions": active,
                "prompt_ids": len(self._versions),
            }


# --------------------------------------------------------------------- #
# 全局单例
# --------------------------------------------------------------------- #

_global_registry: PromptRegistry | None = None
_global_lock = threading.Lock()


def init_registry(
    *,
    yaml_path: Path | None = None,
    overrides_path: Path | None = None,
    legacy_fallback: dict[str, Path] | None = None,
) -> PromptRegistry:
    global _global_registry
    with _global_lock:
        _global_registry = PromptRegistry(
            yaml_path=yaml_path,
            overrides_path=overrides_path,
            legacy_fallback=legacy_fallback,
        )
        _global_registry.load()
        return _global_registry


def get_registry() -> PromptRegistry | None:
    return _global_registry


def reset_registry_for_tests() -> None:
    global _global_registry
    with _global_lock:
        _global_registry = None
