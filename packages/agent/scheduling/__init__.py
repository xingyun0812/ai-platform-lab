"""调度能力子包 — 互斥 / 资源池 / 聚合补全的底层模型（PRD #243，ADR-0009 #244）。"""

from packages.agent.scheduling.aggregator import (
    AggregatedProduct,
    AggregationResult,
    CompletionCallback,
    OutputSchema,
    aggregate_tool_outputs,
    parse_output_schema,
    validate_completeness,
)
from packages.agent.scheduling.mutex import (
    MutexArbitrator,
    MutexConflict,
    MutexDecision,
)
from packages.agent.scheduling.resource_pool import (
    DEFAULT_MAX_CONCURRENT,
    ResourceEvent,
    ResourceHandle,
    ResourcePoolManager,
)
from packages.agent.scheduling.schedule_policy import (
    DEFAULT_TOOL_CLASSIFICATIONS_PATH,
    SchedulePolicyStore,
    SchedulingPolicy,
    load_scheduling_config,
    merge_tool_policy,
    resolve_scheduling_policy,
)

__all__ = [
    "AggregatedProduct",
    "AggregationResult",
    "CompletionCallback",
    "DEFAULT_MAX_CONCURRENT",
    "DEFAULT_TOOL_CLASSIFICATIONS_PATH",
    "MutexArbitrator",
    "MutexConflict",
    "MutexDecision",
    "OutputSchema",
    "ResourceEvent",
    "ResourceHandle",
    "ResourcePoolManager",
    "SchedulePolicyStore",
    "SchedulingPolicy",
    "aggregate_tool_outputs",
    "load_scheduling_config",
    "merge_tool_policy",
    "parse_output_schema",
    "resolve_scheduling_policy",
    "validate_completeness",
]
