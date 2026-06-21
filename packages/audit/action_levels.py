"""动作分级审计 — Phase I #42

工具动作分类 (read_only / write / destructive / network / unknown) 及分类注册表。
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import]

    _YAML_OK = True
except ImportError:  # pragma: no cover
    _YAML_OK = False


# ---------------------------------------------------------------------------
# ActionLevel 常量（StrEnum 替代，兼容 Python 3.9）
# ---------------------------------------------------------------------------

class ActionLevel:
    READ_ONLY = "read_only"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    NETWORK = "network"
    UNKNOWN = "unknown"

    _ALL = {READ_ONLY, WRITE, DESTRUCTIVE, NETWORK, UNKNOWN}

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._ALL


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class ToolActionClassification:
    tool_name: str
    action_level: str
    requires_approval: bool = False
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "action_level": self.action_level,
            "requires_approval": self.requires_approval,
            "description": self.description,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# 启发式分类关键字
# ---------------------------------------------------------------------------

_DESTRUCTIVE_KEYWORDS = ("delete", "drop", "rm", "destroy", "purge", "truncate", "remove")
_WRITE_KEYWORDS = ("create", "update", "write", "send", "put", "post", "insert", "save", "upload", "set")
_READ_KEYWORDS = ("get", "list", "read", "search", "fetch", "query", "find", "show", "describe", "check")
_NETWORK_KEYWORDS = ("http", "request", "webhook", "ping", "download", "crawl", "scrape")


def _heuristic_level(tool_name: str) -> str:
    name_lower = tool_name.lower()
    for kw in _DESTRUCTIVE_KEYWORDS:
        if kw in name_lower:
            return ActionLevel.DESTRUCTIVE
    for kw in _NETWORK_KEYWORDS:
        if kw in name_lower:
            return ActionLevel.NETWORK
    for kw in _WRITE_KEYWORDS:
        if kw in name_lower:
            return ActionLevel.WRITE
    for kw in _READ_KEYWORDS:
        if kw in name_lower:
            return ActionLevel.READ_ONLY
    return ActionLevel.UNKNOWN


# ---------------------------------------------------------------------------
# 内置默认分类
# ---------------------------------------------------------------------------

_DEFAULT_CLASSIFICATIONS: list[ToolActionClassification] = [
    ToolActionClassification(
        tool_name="calc",
        action_level=ActionLevel.READ_ONLY,
        requires_approval=False,
        description="本地计算器工具，纯只读",
    ),
    ToolActionClassification(
        tool_name="get_kb_snippet",
        action_level=ActionLevel.READ_ONLY,
        requires_approval=False,
        description="知识库检索，只读",
    ),
    ToolActionClassification(
        tool_name="search_web_stub",
        action_level=ActionLevel.NETWORK,
        requires_approval=False,
        description="Web 搜索（网络请求）",
    ),
    ToolActionClassification(
        tool_name="httpbin_delay",
        action_level=ActionLevel.NETWORK,
        requires_approval=False,
        description="httpbin 延迟接口（网络请求）",
    ),
    ToolActionClassification(
        tool_name="math_llm_stub",
        action_level=ActionLevel.READ_ONLY,
        requires_approval=False,
        description="数学 LLM stub，只读",
    ),
]


# ---------------------------------------------------------------------------
# ActionClassifier
# ---------------------------------------------------------------------------

class ActionClassifier:
    """线程安全的工具动作分类注册表。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._classifications: dict[str, ToolActionClassification] = {}
        # 加载内置默认分类
        for cls_ in _DEFAULT_CLASSIFICATIONS:
            self._classifications[cls_.tool_name] = cls_

    # ------------------------------------------------------------------
    # 加载 YAML / JSON
    # ------------------------------------------------------------------

    def load_yaml(self, yaml_path: Path | None) -> None:
        """从 YAML 文件加载分类（会覆盖同名工具的已有分类）。"""
        if not _YAML_OK:
            return
        if yaml_path is None or not yaml_path.exists():
            return
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            entries = data.get("classifications", [])
            with self._lock:
                for item in entries:
                    cls_ = ToolActionClassification(
                        tool_name=item["tool_name"],
                        action_level=item.get("action_level", ActionLevel.UNKNOWN),
                        requires_approval=item.get("requires_approval", False),
                        description=item.get("description", ""),
                        metadata=item.get("metadata", {}),
                    )
                    self._classifications[cls_.tool_name] = cls_
        except Exception:  # noqa: BLE001
            pass  # graceful degradation

    def load_json_overrides(self, json_path: Path | None) -> None:
        """从 JSON 文件加载覆盖分类。"""
        if json_path is None or not json_path.exists():
            return
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                entries = json.load(f)
            if isinstance(entries, list):
                with self._lock:
                    for item in entries:
                        cls_ = ToolActionClassification(
                            tool_name=item["tool_name"],
                            action_level=item.get("action_level", ActionLevel.UNKNOWN),
                            requires_approval=item.get("requires_approval", False),
                            description=item.get("description", ""),
                            metadata=item.get("metadata", {}),
                        )
                        self._classifications[cls_.tool_name] = cls_
        except Exception:  # noqa: BLE001
            pass  # graceful degradation

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------

    def classify(self, tool_name: str, arguments: dict | None = None) -> str:  # noqa: ARG002
        """返回 ActionLevel 字符串。优先查表，否则启发式。"""
        with self._lock:
            cls_ = self._classifications.get(tool_name)
        if cls_ is not None:
            return cls_.action_level
        return _heuristic_level(tool_name)

    def register_classification(self, cls_: ToolActionClassification) -> ToolActionClassification:
        with self._lock:
            self._classifications[cls_.tool_name] = cls_
        return cls_

    def get_classification(self, tool_name: str) -> ToolActionClassification | None:
        with self._lock:
            return self._classifications.get(tool_name)

    def list_classifications(self) -> list[ToolActionClassification]:
        with self._lock:
            return list(self._classifications.values())

    def remove_classification(self, tool_name: str) -> bool:
        with self._lock:
            if tool_name in self._classifications:
                del self._classifications[tool_name]
                return True
        return False

    def requires_approval(self, tool_name: str) -> bool:
        """若动作级别为 destructive 或 requires_approval=True，则需要审批。"""
        with self._lock:
            cls_ = self._classifications.get(tool_name)
        if cls_ is not None:
            return cls_.action_level == ActionLevel.DESTRUCTIVE or cls_.requires_approval
        # 启发式：destructive 关键字
        return _heuristic_level(tool_name) == ActionLevel.DESTRUCTIVE


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_classifier_singleton: ActionClassifier | None = None
_singleton_lock = threading.Lock()


def init_classifier(
    yaml_path: Path | None = None,
    overrides_path: Path | None = None,
) -> ActionClassifier:
    global _classifier_singleton
    with _singleton_lock:
        if _classifier_singleton is None:
            c = ActionClassifier()
            c.load_yaml(yaml_path)
            c.load_json_overrides(overrides_path)
            _classifier_singleton = c
    return _classifier_singleton


def get_classifier() -> ActionClassifier | None:
    return _classifier_singleton


def reset_for_tests() -> None:
    global _classifier_singleton
    with _singleton_lock:
        _classifier_singleton = None
