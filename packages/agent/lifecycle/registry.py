"""Agent 生命周期管理注册表 — Phase H #39

存储：
    config/agent_versions.yaml  — git 跟踪的默认版本配置
    data/agent_versions_overrides.json — admin API 运行时修改（不进 git）

生命周期状态：
    draft    → active（activate_version）
    active   → archived（被新版本激活时自动归档）
    * → archived（archive_version 手动归档）

灰度策略：
    all_at_once — 全量切换
    blue_green  — 蓝绿，两版本各保留，手动 traffic split
    canary      — 金丝雀，按百分比流量切分
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("ai_platform.agent.lifecycle")


# --------------------------------------------------------------------- #
# 枚举 / 常量
# --------------------------------------------------------------------- #

class RolloutStrategy:
    """发布策略常量。"""
    ALL_AT_ONCE = "all_at_once"
    BLUE_GREEN = "blue_green"
    CANARY = "canary"

    _VALID = {"all_at_once", "blue_green", "canary"}

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._VALID


# --------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------- #

@dataclass
class AgentVersion:
    """Agent 版本快照。"""
    version_id: str               # 全局唯一 (uuid4)
    agent_id: str                 # 关联的 agent_id
    version: int                  # 自增版本号（per agent）
    spec_snapshot: dict           # AgentSpec.to_dict() 快照
    created_at: float             # 创建时间戳
    created_by: str               # 创建者
    status: str                   # draft | active | archived
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RolloutStatus:
    """Agent 当前发布状态。"""
    agent_id: str
    active_version: str                 # 当前激活的 version_id
    previous_version: str | None        # 上一个激活的 version_id（用于回滚）
    strategy: str                       # RolloutStrategy 值
    traffic_split: dict                 # version_id → percent (0-100)
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------- #

class AgentLifecycleRegistry:
    """Agent 版本生命周期注册表。

    线程安全。从 YAML + JSON overrides 加载初始数据。
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
        # agent_id → list[AgentVersion] (newest last)
        self._versions: dict[str, list[AgentVersion]] = {}
        # agent_id → active version_id
        self._active: dict[str, str] = {}
        # agent_id → RolloutStatus
        self._rollouts: dict[str, RolloutStatus] = {}
        self._loaded = False

    # ----------------------------------------------------------------- #
    # 加载
    # ----------------------------------------------------------------- #

    def load(self) -> None:
        with self._lock:
            self._versions.clear()
            self._active.clear()
            self._rollouts.clear()
            if self._yaml_path and self._yaml_path.is_file():
                try:
                    data = yaml.safe_load(self._yaml_path.read_text(encoding="utf-8"))
                    self._merge_data(data)
                    logger.info(
                        "lifecycle registry loaded yaml=%s agents=%d",
                        self._yaml_path,
                        len(self._versions),
                    )
                except Exception as e:
                    logger.warning("lifecycle yaml load failed: %s", e)
            if self._overrides_path and self._overrides_path.is_file():
                try:
                    data = json.loads(self._overrides_path.read_text(encoding="utf-8"))
                    self._merge_data(data)
                    logger.info(
                        "lifecycle registry loaded overrides=%s agents=%d",
                        self._overrides_path,
                        len(self._versions),
                    )
                except Exception as e:
                    logger.warning("lifecycle overrides load failed: %s", e)
            self._loaded = True

    def _merge_data(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        versions_list = data.get("versions")
        if not isinstance(versions_list, list):
            return
        for item in versions_list:
            ver = self._parse_version(item)
            if ver is None:
                continue
            agent_versions = self._versions.setdefault(ver.agent_id, [])
            # 去重：同一 version_id 不重复添加
            if not any(v.version_id == ver.version_id for v in agent_versions):
                agent_versions.append(ver)
            # 按 version 升序排列
            agent_versions.sort(key=lambda v: v.version)
            if ver.status == "active":
                self._active[ver.agent_id] = ver.version_id
                # 构建或更新 rollout 状态
                rs = self._rollouts.get(ver.agent_id)
                if rs is None:
                    self._rollouts[ver.agent_id] = RolloutStatus(
                        agent_id=ver.agent_id,
                        active_version=ver.version_id,
                        previous_version=None,
                        strategy=RolloutStrategy.ALL_AT_ONCE,
                        traffic_split={ver.version_id: 100},
                        updated_at=ver.created_at,
                    )
        # 加载已持久化的 rollout 状态
        rollouts = data.get("rollouts")
        if isinstance(rollouts, list):
            for item in rollouts:
                rs = self._parse_rollout(item)
                if rs is not None:
                    self._rollouts[rs.agent_id] = rs
                    self._active[rs.agent_id] = rs.active_version

    def _parse_version(self, item: Any) -> AgentVersion | None:
        if not isinstance(item, dict):
            return None
        try:
            return AgentVersion(
                version_id=str(item["version_id"]),
                agent_id=str(item["agent_id"]),
                version=int(item["version"]),
                spec_snapshot=dict(item.get("spec_snapshot", {})),
                created_at=float(item.get("created_at", time.time())),
                created_by=str(item.get("created_by", "system")),
                status=str(item.get("status", "draft")),
                metadata=dict(item.get("metadata", {})),
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("lifecycle version parse failed: %s item=%r", e, item)
            return None

    def _parse_rollout(self, item: Any) -> RolloutStatus | None:
        if not isinstance(item, dict):
            return None
        try:
            return RolloutStatus(
                agent_id=str(item["agent_id"]),
                active_version=str(item["active_version"]),
                previous_version=item.get("previous_version"),
                strategy=str(item.get("strategy", RolloutStrategy.ALL_AT_ONCE)),
                traffic_split=dict(item.get("traffic_split", {})),
                updated_at=float(item.get("updated_at", time.time())),
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("lifecycle rollout parse failed: %s item=%r", e, item)
            return None

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ----------------------------------------------------------------- #
    # 版本管理
    # ----------------------------------------------------------------- #

    def register_version(
        self,
        agent_id: str,
        spec_snapshot: dict,
        created_by: str = "system",
        metadata: dict | None = None,
    ) -> AgentVersion:
        """注册新版本，自动递增版本号。"""
        self._ensure_loaded()
        with self._lock:
            existing = self._versions.get(agent_id, [])
            next_version = (max(v.version for v in existing) + 1) if existing else 1
            ver = AgentVersion(
                version_id=str(uuid.uuid4()),
                agent_id=agent_id,
                version=next_version,
                spec_snapshot=dict(spec_snapshot),
                created_at=time.time(),
                created_by=created_by,
                status="draft",
                metadata=dict(metadata or {}),
            )
            self._versions.setdefault(agent_id, []).append(ver)
            self._persist()
            return ver

    def list_versions(self, agent_id: str) -> list[AgentVersion]:
        """按版本号升序返回某 agent 的所有版本。"""
        self._ensure_loaded()
        with self._lock:
            return list(self._versions.get(agent_id, []))

    def get_version(self, version_id: str) -> AgentVersion | None:
        """按 version_id 查找版本（全局搜索）。"""
        self._ensure_loaded()
        with self._lock:
            for versions in self._versions.values():
                for ver in versions:
                    if ver.version_id == version_id:
                        return ver
            return None

    def activate_version(
        self,
        version_id: str,
        strategy: str = RolloutStrategy.ALL_AT_ONCE,
    ) -> RolloutStatus:
        """激活指定版本，将之前 active 版本归档。

        Returns:
            RolloutStatus — 新的发布状态。

        Raises:
            KeyError: version_id 不存在。
            ValueError: strategy 无效。
        """
        if not RolloutStrategy.is_valid(strategy):
            raise ValueError(f"无效的发布策略: {strategy!r}")
        self._ensure_loaded()
        with self._lock:
            ver = self.get_version(version_id)
            if ver is None:
                raise KeyError(f"version_id {version_id!r} 不存在")
            agent_id = ver.agent_id

            # 归档旧 active 版本
            old_active_id: str | None = self._active.get(agent_id)
            if old_active_id and old_active_id != version_id:
                old_ver = self.get_version(old_active_id)
                if old_ver is not None:
                    old_ver.status = "archived"

            # 激活新版本
            ver.status = "active"
            self._active[agent_id] = version_id

            # 更新 rollout 状态
            prev_rollout = self._rollouts.get(agent_id)
            previous_version = (
                prev_rollout.active_version
                if prev_rollout and prev_rollout.active_version != version_id
                else (prev_rollout.previous_version if prev_rollout else None)
            )
            traffic_split: dict = {}
            if strategy == RolloutStrategy.ALL_AT_ONCE:
                traffic_split = {version_id: 100}
            elif strategy in (RolloutStrategy.BLUE_GREEN, RolloutStrategy.CANARY):
                # 初始 50/50 或 90/10，用户可通过 set_traffic_split 调整
                if previous_version:
                    if strategy == RolloutStrategy.BLUE_GREEN:
                        traffic_split = {previous_version: 50, version_id: 50}
                    else:
                        traffic_split = {previous_version: 90, version_id: 10}
                else:
                    traffic_split = {version_id: 100}

            rs = RolloutStatus(
                agent_id=agent_id,
                active_version=version_id,
                previous_version=previous_version,
                strategy=strategy,
                traffic_split=traffic_split,
                updated_at=time.time(),
            )
            self._rollouts[agent_id] = rs
            self._persist()
            return rs

    def rollback_version(self, agent_id: str) -> RolloutStatus | None:
        """回滚到前一版本。

        Returns:
            RolloutStatus | None — 回滚后的状态；无可回滚版本时返回 None。
        """
        self._ensure_loaded()
        with self._lock:
            rs = self._rollouts.get(agent_id)
            if rs is None or rs.previous_version is None:
                return None
            prev_id = rs.previous_version
            prev_ver = self.get_version(prev_id)
            if prev_ver is None:
                return None
            # 归档当前 active
            cur_ver = self.get_version(rs.active_version)
            if cur_ver is not None:
                cur_ver.status = "archived"
            # 激活 previous
            prev_ver.status = "active"
            self._active[agent_id] = prev_id
            new_rs = RolloutStatus(
                agent_id=agent_id,
                active_version=prev_id,
                previous_version=None,  # 回滚后清空 previous
                strategy=rs.strategy,
                traffic_split={prev_id: 100},
                updated_at=time.time(),
            )
            self._rollouts[agent_id] = new_rs
            self._persist()
            return new_rs

    def get_active(self, agent_id: str) -> AgentVersion | None:
        """获取指定 agent 当前激活版本。"""
        self._ensure_loaded()
        with self._lock:
            active_id = self._active.get(agent_id)
            if active_id is None:
                return None
            return self.get_version(active_id)

    def set_traffic_split(
        self,
        agent_id: str,
        splits: dict,
    ) -> RolloutStatus:
        """设置流量分配（用于 canary / blue_green）。

        splits: {version_id → percent}，合计应为 100。

        Raises:
            KeyError: agent 无发布状态。
            ValueError: 百分比合计不等于 100 或版本不存在。
        """
        self._ensure_loaded()
        with self._lock:
            rs = self._rollouts.get(agent_id)
            if rs is None:
                raise KeyError(f"agent {agent_id!r} 无发布状态，请先激活版本")
            total = sum(int(v) for v in splits.values())
            if total != 100:
                raise ValueError(f"流量分配之和必须等于 100，当前 total={total}")
            for vid in splits:
                if self.get_version(vid) is None:
                    raise ValueError(f"version_id {vid!r} 不存在")
            rs.traffic_split = {k: int(v) for k, v in splits.items()}
            rs.updated_at = time.time()
            self._persist()
            return rs

    def archive_version(self, version_id: str) -> bool:
        """手动归档版本。active 版本不能直接归档（需先激活其他版本）。

        Returns:
            True 成功归档；False 版本不存在或已归档。
        """
        self._ensure_loaded()
        with self._lock:
            ver = self.get_version(version_id)
            if ver is None:
                return False
            if ver.status == "archived":
                return False
            if ver.status == "active":
                # 不允许直接归档 active 版本
                logger.warning("cannot archive active version %s", version_id)
                return False
            ver.status = "archived"
            self._persist()
            return True

    # ----------------------------------------------------------------- #
    # 统计
    # ----------------------------------------------------------------- #

    def stats(self) -> dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            total_agents = len(self._versions)
            total_versions = sum(len(vl) for vl in self._versions.values())
            active_count = len(self._active)
            status_counts: dict[str, int] = {"draft": 0, "active": 0, "archived": 0}
            for vl in self._versions.values():
                for v in vl:
                    status_counts[v.status] = status_counts.get(v.status, 0) + 1
            return {
                "total_agents": total_agents,
                "total_versions": total_versions,
                "active_agents": active_count,
                "by_status": status_counts,
            }

    # ----------------------------------------------------------------- #
    # 持久化
    # ----------------------------------------------------------------- #

    def _persist(self) -> None:
        if not self._overrides_path:
            return
        try:
            self._overrides_path.parent.mkdir(parents=True, exist_ok=True)
            versions_list = [
                v.to_dict()
                for vl in self._versions.values()
                for v in vl
            ]
            rollouts_list = [rs.to_dict() for rs in self._rollouts.values()]
            data = {
                "versions": versions_list,
                "rollouts": rollouts_list,
            }
            self._overrides_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("lifecycle persist failed: %s", e)


# --------------------------------------------------------------------- #
# 全局单例
# --------------------------------------------------------------------- #

_global_registry: AgentLifecycleRegistry | None = None
_global_lock = threading.Lock()


def init_lifecycle_registry(
    *,
    yaml_path: Path | None = None,
    overrides_path: Path | None = None,
) -> AgentLifecycleRegistry:
    """初始化全局注册表并返回实例。"""
    global _global_registry
    with _global_lock:
        _global_registry = AgentLifecycleRegistry(
            yaml_path=yaml_path,
            overrides_path=overrides_path,
        )
        _global_registry.load()
        return _global_registry


def get_lifecycle_registry() -> AgentLifecycleRegistry | None:
    """获取全局注册表（未初始化时返回 None）。"""
    return _global_registry


def reset_lifecycle_registry_for_tests() -> None:
    """测试专用：重置全局单例。"""
    global _global_registry
    with _global_lock:
        _global_registry = None
