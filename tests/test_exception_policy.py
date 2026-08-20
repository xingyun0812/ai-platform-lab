from __future__ import annotations

import asyncio

import pytest

from packages.agent.exception_policy import (
    FailureClass,
    FailurePolicy,
    HierarchicalExceptionPolicy,
    RetryPolicy,
    classify_failure,
    execute_with_retry_policy,
)


class TestFailureClass:
    def test_transient_value(self) -> None:
        assert FailureClass.TRANSIENT.value == "transient"

    def test_fatal_value(self) -> None:
        assert FailureClass.FATAL.value == "fatal"


class TestClassifyFailure:
    def test_timeout_is_transient(self) -> None:
        assert classify_failure("TIMEOUT", "request timed out") == FailureClass.TRANSIENT

    def test_rate_limit_is_transient(self) -> None:
        assert classify_failure("RATE_LIMIT", "too many requests") == FailureClass.TRANSIENT

    def test_upstream_error_is_transient(self) -> None:
        assert classify_failure("UPSTREAM_ERROR", "upstream service unavailable") == FailureClass.TRANSIENT

    def test_http_503_is_transient(self) -> None:
        assert classify_failure("HTTP_ERROR", "503 Service Unavailable") == FailureClass.TRANSIENT

    def test_http_502_is_transient(self) -> None:
        assert classify_failure("HTTP_ERROR", "502 Bad Gateway") == FailureClass.TRANSIENT

    def test_connection_reset_is_transient(self) -> None:
        assert classify_failure("CONNECTION_ERROR", "CONNECTION_RESET") == FailureClass.TRANSIENT

    def test_too_many_requests_is_transient(self) -> None:
        assert classify_failure("429", "TOO_MANY_REQUESTS") == FailureClass.TRANSIENT

    def test_auth_error_is_fatal(self) -> None:
        assert classify_failure("AUTH_ERROR", "invalid API key") == FailureClass.FATAL

    def test_validation_error_is_fatal(self) -> None:
        assert classify_failure("VALIDATION_ERROR", "invalid input format") == FailureClass.FATAL

    def test_empty_code_and_message_is_fatal(self) -> None:
        assert classify_failure("", "") == FailureClass.FATAL

    def test_transient_pattern_in_message_body(self) -> None:
        assert classify_failure("UNKNOWN", "Connection reset by peer") == FailureClass.TRANSIENT

    def test_case_insensitive_matching(self) -> None:
        assert classify_failure("timeout", "upstream timeout") == FailureClass.TRANSIENT


class TestRetryPolicyDefaults:
    def test_defaults(self) -> None:
        policy = RetryPolicy()
        assert policy.max_retries == 3
        assert policy.backoff_base_seconds == 1.0
        assert policy.backoff_max_seconds == 60.0
        assert policy.jitter is True


class TestFailurePolicy:
    def test_default_transient_is_retry_policy(self) -> None:
        policy = FailurePolicy()
        assert isinstance(policy.transient, RetryPolicy)
        assert policy.transient.max_retries == 3

    def test_default_fatal_behavior(self) -> None:
        policy = FailurePolicy()
        assert policy.fatal_behavior == "pause"


class TestHierarchicalExceptionPolicy:
    def test_step_retry_max_2(self) -> None:
        policy = HierarchicalExceptionPolicy()
        assert policy.step_retry.max_retries == 2

    def test_layer_retry_max_1(self) -> None:
        policy = HierarchicalExceptionPolicy()
        assert policy.layer_retry.max_retries == 1

    def test_task_on_fatal_pause(self) -> None:
        policy = HierarchicalExceptionPolicy()
        assert policy.task_on_fatal == "pause"

    def test_dead_letter_enabled(self) -> None:
        policy = HierarchicalExceptionPolicy()
        assert policy.dead_letter_enabled is True


class TestExecuteWithRetryPolicy:
    @staticmethod
    def _run(coro_factory):
        """Run an async test synchronously."""
        return asyncio.run(coro_factory())

    def test_success_no_retry(self) -> None:
        calls = 0

        async def fn() -> str:
            nonlocal calls
            calls += 1
            return "ok"

        result = self._run(lambda: execute_with_retry_policy(fn, RetryPolicy(max_retries=3, jitter=False)))
        assert result == "ok"
        assert calls == 1

    def test_retry_then_succeed(self) -> None:
        calls = 0

        async def fn() -> str:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RuntimeError("transient error")
            return "ok"

        result = self._run(lambda: execute_with_retry_policy(fn, RetryPolicy(max_retries=3, jitter=False)))
        assert result == "ok"
        assert calls == 3

    def test_retry_exhausted(self) -> None:
        calls = 0

        async def fn() -> str:
            nonlocal calls
            calls += 1
            raise RuntimeError("persistent error")

        with pytest.raises(RuntimeError, match="persistent error"):
            self._run(lambda: execute_with_retry_policy(fn, RetryPolicy(max_retries=2, jitter=False)))
        assert calls == 3  # initial + 2 retries

    def test_no_retries_configured(self) -> None:
        calls = 0

        async def fn() -> str:
            nonlocal calls
            calls += 1
            raise ValueError("no retry")

        with pytest.raises(ValueError, match="no retry"):
            self._run(lambda: execute_with_retry_policy(fn, RetryPolicy(max_retries=0, jitter=False)))
        assert calls == 1

    def test_exception_preserves_type(self) -> None:
        async def fn() -> str:
            raise KeyError("missing key")

        with pytest.raises(KeyError, match="missing key"):
            self._run(lambda: execute_with_retry_policy(fn, RetryPolicy(max_retries=1, jitter=False)))