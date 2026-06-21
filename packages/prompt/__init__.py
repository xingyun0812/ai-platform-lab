"""Prompt 版本化注册表 — Phase F #29

对标「Agent 平台架构全景」中的「能力中台 — Prompt 管理」能力。
支持版本化、active 切换、灰度回滚、审计（changelog + created_by）。

核心导出：
    PromptVersion         — 版本数据类
    PromptRegistry        — 注册表（YAML 默认 + JSON overrides）
    init_registry()       — 初始化全局单例
    get_registry()        — 获取全局单例
    render()              — {{var}} 模板渲染
    extract_variables()   — 提取模板变量

Phase F #30 — A/B 实验：
    Experiment / ExperimentVariant / VariantMetrics
    ExperimentStore
    init_experiment_store() / get_experiment_store()
"""

from packages.prompt.experiment import (
    Experiment,
    ExperimentError,
    ExperimentStore,
    ExperimentVariant,
    VariantMetrics,
    get_experiment_store,
    init_experiment_store,
    reset_experiment_store_for_tests,
)
from packages.prompt.registry import (
    PromptRegistry,
    PromptRegistryError,
    PromptVersion,
    get_registry,
    init_registry,
    reset_registry_for_tests,
)
from packages.prompt.render import (
    extract_variables,
    render,
    validate_template,
)

__all__ = [
    "Experiment",
    "ExperimentError",
    "ExperimentStore",
    "ExperimentVariant",
    "PromptRegistry",
    "PromptRegistryError",
    "PromptVersion",
    "VariantMetrics",
    "extract_variables",
    "get_experiment_store",
    "get_registry",
    "init_experiment_store",
    "init_registry",
    "render",
    "reset_experiment_store_for_tests",
    "reset_registry_for_tests",
    "validate_template",
]
