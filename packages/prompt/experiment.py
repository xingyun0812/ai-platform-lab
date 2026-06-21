"""Prompt A/B 实验 — Phase F #30

数据模型：
    ExperimentVariant
        version: int           # registry 中的 prompt 版本
        percent: int           # 0-100，流量占比
    Experiment
        experiment_id: str      # 自动生成
        prompt_id: str
        tenant_id: str          # "global" 或具体租户
        variants: list[ExperimentVariant]
        status: str             # "running" | "stopped" | "promoted"
        min_samples: int         # 自动胜出所需最小样本数
        success_metric: str     # "quality" | "latency" | "tokens"
        winner_margin: float     # 胜出阈值（quality: 0-1, latency: 0-1 比例降低）
        created_at: float
        stopped_at: float | None
        created_by: str

分桶策略：
    hash(experiment_id + tenant_id + session_id_or_query) → 0-99
    按累计 percent 边界落桶；保证同一 session 始终分到同一版本

指标：
    每个 (experiment_id, version) 维护：
    - requests（流量分配次数）
    - latencies_ms（用于 p95）
    - tokens_used
    - errors
    - quality_scores（用户反馈，0-1）

自动胜出：
    当 requests ≥ min_samples 且某 variant 在 success_metric 上超出其他 winner_margin，
    标记 winner 并停止实验；不自动 set_active（需 admin 显式 promote）

存储：
    JSON 文件 data/prompt_experiments.json（git 忽略）
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai_platform.prompt.experiment")


@dataclass
class ExperimentVariant:
    version: int
    percent: int  # 0-100

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Experiment:
    experiment_id: str
    prompt_id: str
    variants: list[ExperimentVariant]
    tenant_id: str = "global"
    status: str = "running"  # running | stopped | promoted
    min_samples: int = 100
    success_metric: str = "quality"  # quality | latency | tokens
    winner_margin: float = 0.1
    winner_version: int | None = None
    created_at: float = field(default_factory=time.time)
    stopped_at: float | None = None
    created_by: str = "admin"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


class ExperimentError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class VariantMetrics:
    requests: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    tokens_used: int = 0
    errors: int = 0
    quality_scores: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "tokens_used": self.tokens_used,
            "errors": self.errors,
            "latency_p95_ms": round(self._p95(self.latencies_ms), 2),
            "quality_avg": round(self._avg(self.quality_scores), 4),
            "quality_samples": len(self.quality_scores),
        }

    @staticmethod
    def _p95(values: list[float]) -> float:
        if not values:
            return 0.0
        sorted_v = sorted(values)
        idx = max(0, min(len(sorted_v) - 1, math.ceil(0.95 * len(sorted_v)) - 1))
        return sorted_v[idx]

    @staticmethod
    def _avg(values: list[float]) -> float:
        if not values:
            return 0.0
        return sum(values) / len(values)


class ExperimentStore:
    """A/B 实验存储 + 指标记录。

    线程安全。所有状态落 JSON 文件。
    """

    MAX_LATENCY_SAMPLES = 500

    def __init__(self, *, storage_path: Path | None = None) -> None:
        self._storage_path = storage_path
        self._lock = threading.RLock()
        self._experiments: dict[str, Experiment] = {}
        # _metrics[(experiment_id, version)] = VariantMetrics
        self._metrics: dict[tuple[str, int], VariantMetrics] = {}
        self._loaded = False

    # ------------------------------------------------------------------ #
    # 加载 / 持久化
    # ------------------------------------------------------------------ #

    def load(self) -> None:
        if not self._storage_path or not self._storage_path.is_file():
            self._loaded = True
            return
        try:
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
            for exp_data in data.get("experiments", []):
                exp = self._parse_experiment(exp_data)
                if exp is not None:
                    self._experiments[exp.experiment_id] = exp
            for m_data in data.get("metrics", []):
                key = (m_data["experiment_id"], int(m_data["version"]))
                m = VariantMetrics(
                    requests=int(m_data.get("requests", 0)),
                    tokens_used=int(m_data.get("tokens_used", 0)),
                    errors=int(m_data.get("errors", 0)),
                    latencies_ms=list(m_data.get("latencies_ms", []))[
                        -self.MAX_LATENCY_SAMPLES :
                    ],
                    quality_scores=list(m_data.get("quality_scores", [])),
                )
                self._metrics[key] = m
            logger.info(
                "experiment store loaded path=%s experiments=%d",
                self._storage_path,
                len(self._experiments),
            )
        except Exception as e:
            logger.warning("experiment store load failed: %s", e)
        self._loaded = True

    def _persist(self) -> None:
        if not self._storage_path:
            return
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            data: dict[str, Any] = {
                "experiments": [e.to_dict() for e in self._experiments.values()],
                "metrics": [],
            }
            for (exp_id, version), m in self._metrics.items():
                data["metrics"].append(
                    {
                        "experiment_id": exp_id,
                        "version": version,
                        "requests": m.requests,
                        "tokens_used": m.tokens_used,
                        "errors": m.errors,
                        "latencies_ms": m.latencies_ms[-self.MAX_LATENCY_SAMPLES :],
                        "quality_scores": m.quality_scores,
                    }
                )
            self._storage_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error("experiment persist failed: %s", e)

    def _parse_experiment(self, data: dict[str, Any]) -> Experiment | None:
        try:
            variants = [
                ExperimentVariant(
                    version=int(v["version"]), percent=int(v["percent"])
                )
                for v in data.get("variants", [])
            ]
            return Experiment(
                experiment_id=str(data["experiment_id"]),
                prompt_id=str(data["prompt_id"]),
                variants=variants,
                tenant_id=str(data.get("tenant_id", "global")),
                status=str(data.get("status", "running")),
                min_samples=int(data.get("min_samples", 100)),
                success_metric=str(data.get("success_metric", "quality")),
                winner_margin=float(data.get("winner_margin", 0.1)),
                winner_version=data.get("winner_version"),
                created_at=float(data.get("created_at", time.time())),
                stopped_at=data.get("stopped_at"),
                created_by=str(data.get("created_by", "admin")),
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("experiment parse failed: %s item=%r", e, data)
            return None

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    def create_experiment(
        self,
        *,
        prompt_id: str,
        variants: list[ExperimentVariant],
        tenant_id: str = "global",
        min_samples: int = 100,
        success_metric: str = "quality",
        winner_margin: float = 0.1,
        created_by: str = "admin",
    ) -> Experiment:
        self._ensure_loaded()
        # 校验
        if len(variants) < 2:
            raise ExperimentError(
                "INVALID_VARIANTS", "至少需要 2 个 variant"
            )
        total = sum(v.percent for v in variants)
        if total != 100:
            raise ExperimentError(
                "INVALID_PERCENT", f"percent 之和必须为 100，当前 {total}"
            )
        for v in variants:
            if v.percent < 0 or v.percent > 100:
                raise ExperimentError(
                    "INVALID_PERCENT", f"percent 必须在 0-100 之间：{v.percent}"
                )
        with self._lock:
            # 同 prompt_id 仅允许一个 running
            for exp in self._experiments.values():
                if (
                    exp.prompt_id == prompt_id
                    and exp.tenant_id == tenant_id
                    and exp.status == "running"
                ):
                    raise ExperimentError(
                        "EXPERIMENT_RUNNING",
                        f"prompt_id={prompt_id} 已有运行中的实验 {exp.experiment_id}",
                    )
            exp_id = self._gen_id(prompt_id, tenant_id)
            exp = Experiment(
                experiment_id=exp_id,
                prompt_id=prompt_id,
                variants=variants,
                tenant_id=tenant_id,
                min_samples=min_samples,
                success_metric=success_metric,
                winner_margin=winner_margin,
                created_by=created_by,
            )
            self._experiments[exp_id] = exp
            # 初始化 metrics
            for v in variants:
                self._metrics[(exp_id, v.version)] = VariantMetrics()
            self._persist()
            logger.info(
                "experiment created id=%s prompt=%s variants=%s",
                exp_id,
                prompt_id,
                [(v.version, v.percent) for v in variants],
            )
            return exp

    def list_experiments(
        self, *, prompt_id: str | None = None, tenant_id: str | None = None
    ) -> list[Experiment]:
        self._ensure_loaded()
        with self._lock:
            out = []
            for exp in self._experiments.values():
                if prompt_id and exp.prompt_id != prompt_id:
                    continue
                if tenant_id and exp.tenant_id != tenant_id:
                    continue
                out.append(exp)
            return sorted(out, key=lambda e: e.created_at, reverse=True)

    def get_experiment(self, experiment_id: str) -> Experiment | None:
        self._ensure_loaded()
        with self._lock:
            return self._experiments.get(experiment_id)

    def get_running(
        self, prompt_id: str, *, tenant_id: str = "global"
    ) -> Experiment | None:
        self._ensure_loaded()
        with self._lock:
            for exp in self._experiments.values():
                if (
                    exp.prompt_id == prompt_id
                    and exp.tenant_id == tenant_id
                    and exp.status == "running"
                ):
                    return exp
            return None

    def stop_experiment(self, experiment_id: str) -> Experiment:
        self._ensure_loaded()
        with self._lock:
            exp = self._experiments.get(experiment_id)
            if exp is None:
                raise ExperimentError("NOT_FOUND", f"experiment {experiment_id} 不存在")
            if exp.status != "running":
                raise ExperimentError(
                    "NOT_RUNNING", f"experiment {experiment_id} 状态={exp.status}"
                )
            exp.status = "stopped"
            exp.stopped_at = time.time()
            self._persist()
            return exp

    # ------------------------------------------------------------------ #
    # 分桶
    # ------------------------------------------------------------------ #

    @staticmethod
    def _gen_id(prompt_id: str, tenant_id: str) -> str:
        h = hashlib.sha1(
            f"{prompt_id}|{tenant_id}|{time.time()}".encode("utf-8")
        ).hexdigest()[:8]
        return f"exp-{prompt_id}-{h}"

    def pick_variant(
        self,
        *,
        prompt_id: str,
        tenant_id: str = "global",
        bucket_key: str,
    ) -> tuple[Experiment, ExperimentVariant] | None:
        """根据 bucket_key 选择 variant。返回 None 表示无运行中实验。"""
        exp = self.get_running(prompt_id, tenant_id=tenant_id)
        if exp is None:
            return None
        # 确定性分桶：hash(exp_id + bucket_key) → 0-99
        h = hashlib.sha256(
            f"{exp.experiment_id}|{bucket_key}".encode("utf-8")
        ).hexdigest()
        bucket = int(h[:8], 16) % 100
        cumulative = 0
        for v in exp.variants:
            cumulative += v.percent
            if bucket < cumulative:
                return exp, v
        # 兜底：返回最后一个
        return exp, exp.variants[-1]

    # ------------------------------------------------------------------ #
    # 指标记录
    # ------------------------------------------------------------------ #

    def record_request(
        self,
        *,
        experiment_id: str,
        version: int,
        latency_ms: float,
        tokens: int = 0,
        error: bool = False,
    ) -> None:
        self._ensure_loaded()
        with self._lock:
            m = self._metrics.get((experiment_id, version))
            if m is None:
                # 实验 ID 不存在或已被清理：忽略
                return
            m.requests += 1
            m.latencies_ms.append(float(latency_ms))
            if len(m.latencies_ms) > self.MAX_LATENCY_SAMPLES:
                del m.latencies_ms[: len(m.latencies_ms) - self.MAX_LATENCY_SAMPLES]
            m.tokens_used += int(tokens)
            if error:
                m.errors += 1
            # 不每次都 _persist（高频写入）；由调用方按需触发或周期 flush
            self._persist()

    def record_quality(
        self,
        *,
        experiment_id: str,
        version: int,
        score: float,
    ) -> None:
        """记录质量反馈（0-1）。"""
        self._ensure_loaded()
        if not (0.0 <= score <= 1.0):
            raise ExperimentError("INVALID_SCORE", f"score 必须在 0-1：{score}")
        with self._lock:
            m = self._metrics.get((experiment_id, version))
            if m is None:
                return
            m.quality_scores.append(float(score))
            self._persist()

    def get_metrics(
        self, *, experiment_id: str, version: int
    ) -> VariantMetrics | None:
        self._ensure_loaded()
        with self._lock:
            return self._metrics.get((experiment_id, version))

    def all_metrics(self, experiment_id: str) -> dict[int, VariantMetrics]:
        self._ensure_loaded()
        with self._lock:
            return {
                v: m
                for (eid, v), m in self._metrics.items()
                if eid == experiment_id
            }

    # ------------------------------------------------------------------ #
    # 自动胜出
    # ------------------------------------------------------------------ #

    def maybe_auto_winner(self, experiment_id: str) -> int | None:
        """检查是否达到自动胜出条件；返回 winner_version 或 None。"""
        self._ensure_loaded()
        with self._lock:
            exp = self._experiments.get(experiment_id)
            if exp is None or exp.status != "running":
                return None
            metrics = self.all_metrics(experiment_id)
            # 所有 variant 都达到 min_samples
            if not all(
                metrics.get(v.version) and metrics[v.version].requests >= exp.min_samples
                for v in exp.variants
            ):
                return None
            # 计算每个 variant 的核心指标
            scores: dict[int, float] = {}
            for v in exp.variants:
                m = metrics.get(v.version)
                if m is None:
                    continue
                if exp.success_metric == "quality":
                    scores[v.version] = m._avg(m.quality_scores)
                elif exp.success_metric == "latency":
                    p95 = m._p95(m.latencies_ms)
                    scores[v.version] = -p95  # 越小越好，取负
                elif exp.success_metric == "tokens":
                    avg_tokens = (
                        m.tokens_used / m.requests if m.requests > 0 else 0
                    )
                    scores[v.version] = -avg_tokens
                else:
                    return None
            if not scores:
                return None
            sorted_versions = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            if len(sorted_versions) < 2:
                return None
            best_v, best_s = sorted_versions[0]
            second_v, second_s = sorted_versions[1]
            # 防御除零
            margin_denom = abs(second_s) if abs(second_s) > 1e-9 else 1.0
            relative_margin = (best_s - second_s) / margin_denom
            if relative_margin >= exp.winner_margin:
                exp.winner_version = best_v
                exp.status = "stopped"
                exp.stopped_at = time.time()
                self._persist()
                logger.info(
                    "experiment auto-winner id=%s version=%d margin=%.4f",
                    experiment_id,
                    best_v,
                    relative_margin,
                )
                return best_v
            return None

    def promote_winner(self, experiment_id: str) -> int:
        """手动将 winner_version 提升为 active（不在此处调 registry，由路由层做）。"""
        self._ensure_loaded()
        with self._lock:
            exp = self._experiments.get(experiment_id)
            if exp is None:
                raise ExperimentError("NOT_FOUND", f"experiment {experiment_id} 不存在")
            if exp.winner_version is None:
                raise ExperimentError(
                    "NO_WINNER", f"experiment {experiment_id} 无 winner_version"
                )
            exp.status = "promoted"
            exp.stopped_at = time.time()
            self._persist()
            return exp.winner_version

    # ------------------------------------------------------------------ #
    # 调试
    # ------------------------------------------------------------------ #

    def stats(self) -> dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            running = sum(1 for e in self._experiments.values() if e.status == "running")
            stopped = sum(1 for e in self._experiments.values() if e.status == "stopped")
            promoted = sum(1 for e in self._experiments.values() if e.status == "promoted")
            return {
                "total_experiments": len(self._experiments),
                "running": running,
                "stopped": stopped,
                "promoted": promoted,
            }


# --------------------------------------------------------------------- #
# 全局单例
# --------------------------------------------------------------------- #

_global_store: ExperimentStore | None = None
_global_lock = threading.Lock()


def init_experiment_store(*, storage_path: Path | None = None) -> ExperimentStore:
    global _global_store
    with _global_lock:
        _global_store = ExperimentStore(storage_path=storage_path)
        _global_store.load()
        return _global_store


def get_experiment_store() -> ExperimentStore | None:
    return _global_store


def reset_experiment_store_for_tests() -> None:
    global _global_store
    with _global_lock:
        _global_store = None
