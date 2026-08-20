from __future__ import annotations

import pytest

from packages.agent.state_machine import (
    StateTransitionError,
    StepStatus,
    TaskStatus,
    validate_step_transition,
    validate_task_transition,
)


class TestTaskStatusEnum:
    def test_value_mapping(self) -> None:
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.PAUSED.value == "paused"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.CANCELLED.value == "cancelled"

    def test_from_string(self) -> None:
        assert TaskStatus("pending") == TaskStatus.PENDING
        assert TaskStatus("running") == TaskStatus.RUNNING
        assert TaskStatus("paused") == TaskStatus.PAUSED
        assert TaskStatus("completed") == TaskStatus.COMPLETED
        assert TaskStatus("failed") == TaskStatus.FAILED
        assert TaskStatus("cancelled") == TaskStatus.CANCELLED

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError):
            TaskStatus("invalid")


class TestStepStatusEnum:
    def test_value_mapping(self) -> None:
        assert StepStatus.PENDING.value == "pending"
        assert StepStatus.RUNNING.value == "running"
        assert StepStatus.COMPLETED.value == "completed"
        assert StepStatus.FAILED.value == "failed"
        assert StepStatus.SKIPPED.value == "skipped"

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError):
            StepStatus("invalid")


class TestValidateTaskTransition:
    def test_pending_to_running(self) -> None:
        assert validate_task_transition(TaskStatus.PENDING, TaskStatus.RUNNING) is True

    def test_pending_to_cancelled(self) -> None:
        assert validate_task_transition(TaskStatus.PENDING, TaskStatus.CANCELLED) is True

    def test_pending_to_completed_invalid(self) -> None:
        assert validate_task_transition(TaskStatus.PENDING, TaskStatus.COMPLETED) is False

    def test_pending_to_paused_invalid(self) -> None:
        assert validate_task_transition(TaskStatus.PENDING, TaskStatus.PAUSED) is False

    def test_pending_to_failed_invalid(self) -> None:
        assert validate_task_transition(TaskStatus.PENDING, TaskStatus.FAILED) is False

    def test_running_to_paused(self) -> None:
        assert validate_task_transition(TaskStatus.RUNNING, TaskStatus.PAUSED) is True

    def test_running_to_completed(self) -> None:
        assert validate_task_transition(TaskStatus.RUNNING, TaskStatus.COMPLETED) is True

    def test_running_to_failed(self) -> None:
        assert validate_task_transition(TaskStatus.RUNNING, TaskStatus.FAILED) is True

    def test_running_to_cancelled(self) -> None:
        assert validate_task_transition(TaskStatus.RUNNING, TaskStatus.CANCELLED) is True

    def test_running_to_pending_invalid(self) -> None:
        assert validate_task_transition(TaskStatus.RUNNING, TaskStatus.PENDING) is False

    def test_running_to_running_invalid(self) -> None:
        assert validate_task_transition(TaskStatus.RUNNING, TaskStatus.RUNNING) is False

    def test_paused_to_running(self) -> None:
        assert validate_task_transition(TaskStatus.PAUSED, TaskStatus.RUNNING) is True

    def test_paused_to_cancelled(self) -> None:
        assert validate_task_transition(TaskStatus.PAUSED, TaskStatus.CANCELLED) is True

    def test_paused_to_completed_invalid(self) -> None:
        assert validate_task_transition(TaskStatus.PAUSED, TaskStatus.COMPLETED) is False

    def test_paused_to_paused_invalid(self) -> None:
        assert validate_task_transition(TaskStatus.PAUSED, TaskStatus.PAUSED) is False

    def test_completed_is_terminal(self) -> None:
        assert validate_task_transition(TaskStatus.COMPLETED, TaskStatus.PENDING) is False
        assert validate_task_transition(TaskStatus.COMPLETED, TaskStatus.RUNNING) is False
        assert validate_task_transition(TaskStatus.COMPLETED, TaskStatus.PAUSED) is False
        assert validate_task_transition(TaskStatus.COMPLETED, TaskStatus.COMPLETED) is False
        assert validate_task_transition(TaskStatus.COMPLETED, TaskStatus.FAILED) is False
        assert validate_task_transition(TaskStatus.COMPLETED, TaskStatus.CANCELLED) is False

    def test_failed_to_running(self) -> None:
        assert validate_task_transition(TaskStatus.FAILED, TaskStatus.RUNNING) is True

    def test_failed_to_other_invalid(self) -> None:
        assert validate_task_transition(TaskStatus.FAILED, TaskStatus.PENDING) is False
        assert validate_task_transition(TaskStatus.FAILED, TaskStatus.COMPLETED) is False
        assert validate_task_transition(TaskStatus.FAILED, TaskStatus.PAUSED) is False
        assert validate_task_transition(TaskStatus.FAILED, TaskStatus.CANCELLED) is False

    def test_cancelled_is_terminal(self) -> None:
        assert validate_task_transition(TaskStatus.CANCELLED, TaskStatus.PENDING) is False
        assert validate_task_transition(TaskStatus.CANCELLED, TaskStatus.RUNNING) is False
        assert validate_task_transition(TaskStatus.CANCELLED, TaskStatus.COMPLETED) is False
        assert validate_task_transition(TaskStatus.CANCELLED, TaskStatus.FAILED) is False
        assert validate_task_transition(TaskStatus.CANCELLED, TaskStatus.CANCELLED) is False


class TestValidateStepTransition:
    def test_pending_to_running(self) -> None:
        assert validate_step_transition(StepStatus.PENDING, StepStatus.RUNNING) is True

    def test_pending_to_skipped(self) -> None:
        assert validate_step_transition(StepStatus.PENDING, StepStatus.SKIPPED) is True

    def test_pending_to_failed(self) -> None:
        assert validate_step_transition(StepStatus.PENDING, StepStatus.FAILED) is True

    def test_pending_to_completed_invalid(self) -> None:
        assert validate_step_transition(StepStatus.PENDING, StepStatus.COMPLETED) is False

    def test_running_to_completed(self) -> None:
        assert validate_step_transition(StepStatus.RUNNING, StepStatus.COMPLETED) is True

    def test_running_to_failed(self) -> None:
        assert validate_step_transition(StepStatus.RUNNING, StepStatus.FAILED) is True

    def test_running_to_pending_invalid(self) -> None:
        assert validate_step_transition(StepStatus.RUNNING, StepStatus.PENDING) is False

    def test_running_to_skipped_invalid(self) -> None:
        assert validate_step_transition(StepStatus.RUNNING, StepStatus.SKIPPED) is False

    def test_completed_is_terminal(self) -> None:
        assert validate_step_transition(StepStatus.COMPLETED, StepStatus.PENDING) is False
        assert validate_step_transition(StepStatus.COMPLETED, StepStatus.RUNNING) is False
        assert validate_step_transition(StepStatus.COMPLETED, StepStatus.COMPLETED) is False
        assert validate_step_transition(StepStatus.COMPLETED, StepStatus.FAILED) is False
        assert validate_step_transition(StepStatus.COMPLETED, StepStatus.SKIPPED) is False

    def test_failed_to_pending(self) -> None:
        assert validate_step_transition(StepStatus.FAILED, StepStatus.PENDING) is True

    def test_failed_to_running(self) -> None:
        assert validate_step_transition(StepStatus.FAILED, StepStatus.RUNNING) is True

    def test_failed_to_completed_invalid(self) -> None:
        assert validate_step_transition(StepStatus.FAILED, StepStatus.COMPLETED) is False

    def test_failed_to_skipped_invalid(self) -> None:
        assert validate_step_transition(StepStatus.FAILED, StepStatus.SKIPPED) is False

    def test_skipped_is_terminal(self) -> None:
        assert validate_step_transition(StepStatus.SKIPPED, StepStatus.PENDING) is False
        assert validate_step_transition(StepStatus.SKIPPED, StepStatus.RUNNING) is False
        assert validate_step_transition(StepStatus.SKIPPED, StepStatus.COMPLETED) is False
        assert validate_step_transition(StepStatus.SKIPPED, StepStatus.FAILED) is False
        assert validate_step_transition(StepStatus.SKIPPED, StepStatus.SKIPPED) is False


class TestStateTransitionError:
    def test_default_message(self) -> None:
        err = StateTransitionError("task", TaskStatus.PENDING, TaskStatus.COMPLETED)
        assert err.entity == "task"
        assert err.current == TaskStatus.PENDING
        assert err.attempted == TaskStatus.COMPLETED
        assert "PENDING" in str(err)
        assert "COMPLETED" in str(err)

    def test_custom_message(self) -> None:
        err = StateTransitionError(
            "task",
            TaskStatus.PENDING,
            TaskStatus.COMPLETED,
            message="custom error",
        )
        assert str(err) == "custom error"

    def test_is_value_error(self) -> None:
        err = StateTransitionError("task", TaskStatus.PENDING, TaskStatus.COMPLETED)
        assert isinstance(err, ValueError)
