"""packages/agent/react_resume_loader.py — S4: ReAct resume context loader.

Loads the latest ReAct checkpoint for a task and reconstructs the full
ReactResumeContext that can be injected directly into run_react_loop()
to resume from where the task left off.

The resume path works because:
1. run_react_loop() with task_id saves new checkpoints on each tool round
2. execute_tool() with execution_key skips idempotent tool calls
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from packages.contracts.agent_schemas import ReasoningTraceRecord, ToolCallRecord

logger = logging.getLogger("ai_platform.agent.react_resume_loader")

__all__ = [
    "ReactResumeContext",
    "load_react_resume_context",
]


# ---------------------------------------------------------------------------
# Resume context
# ---------------------------------------------------------------------------


@dataclass
class ReactResumeContext:
    """Reconstructed ReAct loop state for resuming a long-running task.

    This context mirrors the state that run_react_loop() tracks as local
    variables (working_messages, trace, budget_meta etc.) so that the
    resume path can inject them directly into the loop without rebuilding.
    """

    messages: list[dict[str, Any]] = field(default_factory=list)
    """Messages to be passed into run_react_loop as the initial message list.
    Already includes prior assistant + tool messages."""

    session_messages: list[dict[str, Any]] = field(default_factory=list)
    """Full session messages for session state persistence."""

    trace: list[ToolCallRecord] = field(default_factory=list)
    """Accumulated tool call records from prior steps."""

    reasoning_trace: list[ReasoningTraceRecord] = field(default_factory=list)
    """Accumulated reasoning trace records from prior steps."""

    budget_meta: dict[str, Any] = field(default_factory=dict)
    """ContextBudgetMeta serialized as dict for serialization safety."""

    resolved_model: str = ""
    """Model that was resolved during the original run."""

    reflect_remaining: int = 0
    """Remaining reflection retry count."""

    runtime_truncated_tools: int = 0
    """Count of tool results truncated at runtime."""

    resume_step: int = 0
    """Next step number to use (one past the checkpoint step)."""


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _record_from_dict(d: dict[str, Any]) -> ToolCallRecord:
    """Build a ToolCallRecord from a dict, tolerating partial data."""
    return ToolCallRecord(
        tool_name=d.get("tool_name", ""),
        arguments=d.get("arguments", {}),
        status=d.get("status", "completed"),
        result=d.get("result"),
        error=d.get("error"),
        latency_ms=float(d.get("latency_ms", 0)),
        attempt=int(d.get("attempt", 0)),
        quality_gate=d.get("quality_gate"),
    )


def _reasoning_record_from_dict(d: dict[str, Any]) -> ReasoningTraceRecord:
    """Build a ReasoningTraceRecord from a dict."""
    return ReasoningTraceRecord(
        step=int(d.get("step", 0)),
        thinking=d.get("thinking", ""),
        visible_content=d.get("visible_content"),
    )


async def load_react_resume_context(task_id: str) -> ReactResumeContext | None:
    """Load the latest ReAct checkpoint for *task_id* and return a
    ReactResumeContext ready for injection into run_react_loop().

    Returns *None* when no checkpoint exists for the given task.
    """
    from packages.agent.react_checkpoint import load_latest_react_checkpoint

    checkpoint = await load_latest_react_checkpoint(task_id)
    if checkpoint is None:
        logger.info("no checkpoint found for task %s", task_id)
        return None

    # Rebuild messages from checkpoint — these already include prior
    # assistant + tool messages and are ready to pass to run_react_loop.
    messages = list(checkpoint.messages)
    session_messages = list(checkpoint.messages)

    # Deserialize trace records
    trace: list[ToolCallRecord] = []
    for d in checkpoint.trace:
        try:
            trace.append(_record_from_dict(d))
        except Exception as exc:
            logger.warning("failed to deserialize trace record: %s", exc)

    # Deserialize reasoning trace records
    reasoning_trace: list[ReasoningTraceRecord] = []
    for d in checkpoint.reasoning_trace:
        try:
            reasoning_trace.append(_reasoning_record_from_dict(d))
        except Exception as exc:
            logger.warning("failed to deserialize reasoning trace: %s", exc)

    # Budget meta as plain dict
    budget_meta = dict(checkpoint.budget_meta)

    logger.info(
        "resume context loaded task=%s step=%d messages=%d trace=%d reasoning=%d",
        task_id,
        checkpoint.step,
        len(messages),
        len(trace),
        len(reasoning_trace),
    )

    return ReactResumeContext(
        messages=messages,
        session_messages=session_messages,
        trace=trace,
        reasoning_trace=reasoning_trace,
        budget_meta=budget_meta,
        resolved_model=checkpoint.resolved_model or "",
        reflect_remaining=checkpoint.reflect_remaining,
        runtime_truncated_tools=checkpoint.runtime_truncated_tools,
        resume_step=checkpoint.step + 1,
    )
