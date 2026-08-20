from __future__ import annotations

import enum
import logging
import random
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ai_platform.agent.exception_policy")


class FailureClass(enum.Enum):
    TRANSIENT = "transient"
    FATAL = "fatal"


@dataclass
class RetryPolicy:
    """Retry policy with exponential backoff and jitter."""

    max_retries: int = 3
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    jitter: bool = True


@dataclass
class FailurePolicy:
    """Per-failure-class policy."""

    transient: RetryPolicy = field(default_factory=RetryPolicy)
    fatal_behavior: str = "pause"


@dataclass
class HierarchicalExceptionPolicy:
    """Hierarchical policy for step/layer/task exception handling."""

    step_retry: RetryPolicy = field(default_factory=lambda: RetryPolicy(max_retries=2))
    layer_retry: RetryPolicy = field(default_factory=lambda: RetryPolicy(max_retries=1))
    task_on_fatal: str = "pause"
    dead_letter_enabled: bool = True


_TRANSIENT_PATTERNS = [
    "TIMEOUT",
    "RATE_LIMIT",
    "UPSTREAM_ERROR",
    "503",
    "502",
    "CONNECTION_RESET",
    "TOO_MANY_REQUESTS",
]


def _normalize(text: str) -> str:
    """Normalize text for pattern matching: uppercase, replace underscores with spaces."""
    return text.upper().replace("_", " ")


def classify_failure(error_code: str, error_message: str) -> FailureClass:
    """Classify a failure as TRANSIENT or FATAL based on error patterns.

    Args:
        error_code: A short error code string (e.g., "TIMEOUT", "RATE_LIMIT").
        error_message: Full error message to scan for patterns.

    Returns:
        FailureClass.TRANSIENT if the error matches known transient patterns,
        FailureClass.FATAL otherwise.
    """
    combined = _normalize(f"{error_code} {error_message}")
    for pattern in _TRANSIENT_PATTERNS:
        normalized_pattern = _normalize(pattern)
        if normalized_pattern in combined:
            return FailureClass.TRANSIENT
    return FailureClass.FATAL


async def execute_with_retry_policy(
    coro_factory: Callable[[], Coroutine[Any, Any, Any]],
    policy: RetryPolicy,
) -> Any:
    """Execute an async callable with retry logic.

    Uses exponential backoff with optional jitter.

    Args:
        coro_factory: A zero-argument callable that returns a coroutine.
        policy: The RetryPolicy controlling retry behavior.

    Returns:
        The result of the successful coroutine execution.

    Raises:
        The last exception encountered if all retries are exhausted.
    """
    last_exception: Exception | None = None

    for attempt in range(policy.max_retries + 1):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exception = exc
            if attempt < policy.max_retries:
                delay = _compute_backoff(attempt, policy)
                logger.warning(
                    "Retry attempt %d/%d failed: %s. Waiting %.2fs before next attempt.",
                    attempt + 1,
                    policy.max_retries,
                    exc,
                    delay,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "All %d retry attempts exhausted. Last error: %s",
                    policy.max_retries,
                    exc,
                )

    raise last_exception  # type: ignore[misc]


def _compute_backoff(attempt: int, policy: RetryPolicy) -> float:
    """Compute exponential backoff with optional jitter."""
    delay = policy.backoff_base_seconds * (2**attempt)
    delay = min(delay, policy.backoff_max_seconds)
    if policy.jitter:
        delay *= random.uniform(0.5, 1.5)
    return delay


__all__ = [
    "FailureClass",
    "RetryPolicy",
    "FailurePolicy",
    "HierarchicalExceptionPolicy",
    "classify_failure",
    "execute_with_retry_policy",
]
