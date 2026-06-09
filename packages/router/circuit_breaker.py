from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class CircuitBreaker:
    """按 key（如 model 名）维护熔断状态。"""

    failure_threshold: int = 5
    recovery_seconds: float = 30.0

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._half_open: set[str] = set()

    def _state(self, key: str) -> str:
        if key in self._half_open:
            return "half_open"
        opened = self._opened_at.get(key)
        if opened is None:
            return "closed"
        if time.monotonic() - opened >= self.recovery_seconds:
            self._half_open.add(key)
            return "half_open"
        return "open"

    def allow(self, key: str) -> tuple[bool, str]:
        with self._lock:
            state = self._state(key)
            if state == "open":
                return False, "open"
            return True, state

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._opened_at.pop(key, None)
            self._half_open.discard(key)

    def record_failure(self, key: str) -> str:
        with self._lock:
            if key in self._half_open:
                self._opened_at[key] = time.monotonic()
                self._half_open.discard(key)
                return "open"
            count = self._failures.get(key, 0) + 1
            self._failures[key] = count
            if count >= self.failure_threshold:
                self._opened_at[key] = time.monotonic()
                return "open"
            return "closed"


_breaker: CircuitBreaker | None = None


def get_circuit_breaker() -> CircuitBreaker:
    global _breaker
    if _breaker is None:
        _breaker = CircuitBreaker()
    return _breaker
