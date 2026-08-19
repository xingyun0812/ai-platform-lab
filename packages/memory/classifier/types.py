from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClassResult:
    class_label: str  # "preference" | "factual" | "ephemeral" | "noise"
    confidence: float = 0.5
    source: str = "rule"  # "rule" | "llm" | "default"
    reason: str = ""


__all__ = ["ClassResult"]
