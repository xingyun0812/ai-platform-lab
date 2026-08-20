from __future__ import annotations

import enum
import logging
from typing import Any

logger = logging.getLogger("ai_platform.agent.state_machine")


class TaskStatus(enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def _missing_(cls, value: object) -> TaskStatus | None:
        if isinstance(value, str):
            for member in cls:
                if member.value == value:
                    return member
        return None


class StepStatus(enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

    @classmethod
    def _missing_(cls, value: object) -> StepStatus | None:
        if isinstance(value, str):
            for member in cls:
                if member.value == value:
                    return member
        return None


_TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {TaskStatus.PAUSED, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.PAUSED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: {TaskStatus.RUNNING},
    TaskStatus.CANCELLED: set(),
}

_STEP_TRANSITIONS: dict[StepStatus, set[StepStatus]] = {
    StepStatus.PENDING: {StepStatus.RUNNING, StepStatus.SKIPPED, StepStatus.FAILED},
    StepStatus.RUNNING: {StepStatus.COMPLETED, StepStatus.FAILED},
    StepStatus.COMPLETED: set(),
    StepStatus.FAILED: {StepStatus.PENDING, StepStatus.RUNNING},
    StepStatus.SKIPPED: set(),
}


class StateTransitionError(ValueError):
    """Raised when an invalid state transition is attempted."""

    def __init__(
        self,
        entity: str,
        current: Any,
        attempted: Any,
        message: str | None = None,
    ) -> None:
        self.entity = entity
        self.current = current
        self.attempted = attempted
        if message is None:
            message = (
                f"Invalid {entity} transition: {current!r} -> {attempted!r}"
            )
        super().__init__(message)


def validate_task_transition(current: TaskStatus, next_status: TaskStatus) -> bool:
    """Validate a task status transition.

    Args:
        current: The current TaskStatus.
        next_status: The desired next TaskStatus.

    Returns:
        True if the transition is valid, False otherwise.
    """
    allowed = _TASK_TRANSITIONS.get(current, set())
    return next_status in allowed


def validate_step_transition(current: StepStatus, next_status: StepStatus) -> bool:
    """Validate a step status transition.

    Args:
        current: The current StepStatus.
        next_status: The desired next StepStatus.

    Returns:
        True if the transition is valid, False otherwise.
    """
    allowed = _STEP_TRANSITIONS.get(current, set())
    return next_status in allowed


__all__ = [
    "TaskStatus",
    "StepStatus",
    "StateTransitionError",
    "validate_task_transition",
    "validate_step_transition",
]
