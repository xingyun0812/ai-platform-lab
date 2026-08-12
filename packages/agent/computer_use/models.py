from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ComputerUseConfig:
    """Computer Use 策略配置。"""

    enabled: bool = True
    max_steps: int = 10
    timeout_seconds: float = 120.0
    sandbox_mode: str = "process"  # process | docker
    display: str | None = None  # DISPLAY env var
    action_space: tuple[str, ...] = (
        "click", "type", "key", "scroll", "move", "screenshot", "done",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_steps": self.max_steps,
            "timeout_seconds": self.timeout_seconds,
            "sandbox_mode": self.sandbox_mode,
            "display": self.display,
        }


@dataclass
class ScreenState:
    """当前屏幕状态。"""

    screenshot_base64: str
    width: int
    height: int
    step: int = 0


@dataclass
class ActionResult:
    """单步动作执行结果。"""

    action_type: str  # click | type | key | scroll | move | screenshot | done
    description: str = ""
    x: int | None = None
    y: int | None = None
    text: str | None = None
    key: str | None = None
    dx: int | None = None
    dy: int | None = None
    error: str | None = None
    screenshot_before: str | None = None  # base64
    screenshot_after: str | None = None  # base64
    llm_reasoning: str | None = None  # LLM 决策理由

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "action_type": self.action_type,
            "description": self.description,
            "error": self.error,
        }
        if self.x is not None:
            d["x"] = self.x
        if self.y is not None:
            d["y"] = self.y
        if self.text is not None:
            d["text"] = self.text
        if self.key is not None:
            d["key"] = self.key
        if self.dx is not None:
            d["dx"] = self.dx
        if self.dy is not None:
            d["dy"] = self.dy
        if self.llm_reasoning is not None:
            d["llm_reasoning"] = self.llm_reasoning
        return d


@dataclass
class ComputerUseResult:
    """Computer Use 最终结果。"""

    task: str
    final_answer: str | None
    steps: list[ActionResult] = field(default_factory=list)
    success: bool = False
    execution_time_ms: float = 0.0
    trace: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "final_answer": self.final_answer,
            "steps": [s.to_dict() for s in self.steps],
            "success": self.success,
            "execution_time_ms": self.execution_time_ms,
            "trace": self.trace,
            "error": self.error,
        }
