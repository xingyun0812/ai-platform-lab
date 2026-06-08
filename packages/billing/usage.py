from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int


def parse_token_usage(body: dict[str, Any] | None) -> TokenUsage | None:
    """从 OpenAI 兼容 chat/completions 响应解析 usage。"""
    if not body or not isinstance(body, dict):
        return None
    raw = body.get("usage")
    if not isinstance(raw, dict):
        return None
    prompt = raw.get("prompt_tokens")
    completion = raw.get("completion_tokens")
    total = raw.get("total_tokens")
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return None
    if not isinstance(total, int):
        total = prompt + completion
    return TokenUsage(
        input_tokens=prompt,
        output_tokens=completion,
        total_tokens=total,
    )
