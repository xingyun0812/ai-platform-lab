"""工作流存储 — YAML 默认 + JSON overrides。"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from packages.agent.orchestrator.graph import (
    GraphEdge,
    GraphNode,
    Workflow,
    parse_workflow,
)

logger = logging.getLogger("ai_platform.orchestrator.store")


class WorkflowStore:
    """工作流注册表。"""

    def __init__(
        self,
        *,
        yaml_path: Path | None = None,
        overrides_path: Path | None = None,
    ) -> None:
        self._yaml_path = yaml_path
        self._overrides_path = overrides_path
        self._lock = threading.RLock()
        self._workflows: dict[str, Workflow] = {}
        self._metadata: dict[str, dict[str, Any]] = {}
        self._loaded = False

    def load(self) -> None:
        with self._lock:
            self._workflows.clear()
            self._metadata.clear()
            if self._yaml_path and self._yaml_path.is_file():
                try:
                    data = yaml.safe_load(self._yaml_path.read_text(encoding="utf-8"))
                    self._merge(data, source="yaml")
                except Exception as e:
                    logger.warning("workflow yaml load failed: %s", e)
            if self._overrides_path and self._overrides_path.is_file():
                try:
                    data = json.loads(self._overrides_path.read_text(encoding="utf-8"))
                    self._merge(data, source="overrides")
                except Exception as e:
                    logger.warning("workflow overrides load failed: %s", e)
            self._loaded = True
            logger.info(
                "workflow store loaded workflows=%d", len(self._workflows)
            )

    def _merge(self, data: Any, *, source: str) -> None:
        if not isinstance(data, dict):
            return
        workflows = data.get("workflows")
        if not isinstance(workflows, list):
            return
        for item in workflows:
            if not isinstance(item, dict):
                continue
            try:
                wf = parse_workflow(item)
                self._workflows[wf.workflow_id] = wf
                self._metadata[wf.workflow_id] = {
                    "created_by": item.get("created_by", "system"),
                    "created_at": float(item.get("created_at", time.time())),
                    "source": source,
                }
            except Exception as e:
                logger.warning("workflow parse failed: %s item=%r", e, item.get("workflow_id"))

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def list_workflows(self) -> list[Workflow]:
        self._ensure_loaded()
        with self._lock:
            return [self._workflows[wid] for wid in sorted(self._workflows.keys())]

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        self._ensure_loaded()
        with self._lock:
            return self._workflows.get(workflow_id)

    def add_workflow(
        self,
        wf: Workflow,
        *,
        created_by: str = "system",
    ) -> Workflow:
        self._ensure_loaded()
        with self._lock:
            self._workflows[wf.workflow_id] = wf
            self._metadata[wf.workflow_id] = {
                "created_by": created_by,
                "created_at": time.time(),
                "source": "api",
            }
            self._persist()
            return wf

    def remove_workflow(self, workflow_id: str) -> bool:
        self._ensure_loaded()
        with self._lock:
            if workflow_id not in self._workflows:
                return False
            del self._workflows[workflow_id]
            self._metadata.pop(workflow_id, None)
            self._persist()
            return True

    def _persist(self) -> None:
        if not self._overrides_path:
            return
        try:
            self._overrides_path.parent.mkdir(parents=True, exist_ok=True)
            # 仅持久化 API 创建的（source=api），避免覆盖 yaml
            api_workflows: list[dict[str, Any]] = []
            for wid, wf in self._workflows.items():
                meta = self._metadata.get(wid, {})
                if meta.get("source") == "api":
                    data = wf.to_dict()
                    data["created_by"] = meta.get("created_by", "system")
                    data["created_at"] = meta.get("created_at", time.time())
                    api_workflows.append(data)
            payload = {"workflows": api_workflows}
            self._overrides_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error("workflow persist failed: %s", e)

    def stats(self) -> dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            return {
                "total_workflows": len(self._workflows),
                "api_created": sum(
                    1 for m in self._metadata.values() if m.get("source") == "api"
                ),
            }


# --------------------------------------------------------------------- #
# 全局单例
# --------------------------------------------------------------------- #

_global_store: WorkflowStore | None = None
_global_lock = threading.Lock()


def init_workflow_store(
    *,
    yaml_path: Path | None = None,
    overrides_path: Path | None = None,
) -> WorkflowStore | None:
    global _global_store
    with _global_lock:
        if not yaml_path:
            return _global_store
        _global_store = WorkflowStore(
            yaml_path=yaml_path,
            overrides_path=overrides_path,
        )
        _global_store.load()
        return _global_store


def get_workflow_store() -> WorkflowStore | None:
    return _global_store


def reset_workflow_store_for_tests() -> None:
    global _global_store
    with _global_lock:
        _global_store = None
