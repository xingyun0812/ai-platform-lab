from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolEnvelope:
    ok: bool
    data: Any
    error_code: str | None
    quality_score: float | None
    raw: str


def success_envelope(data: Any, *, quality_score: float = 1.0) -> str:
    return json.dumps(
        {
            "ok": True,
            "data": data,
            "error_code": None,
            "quality_score": quality_score,
        },
        ensure_ascii=False,
    )


def failure_envelope(*, error_code: str, message: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "data": {"message": message},
            "error_code": error_code,
            "quality_score": 0.0,
        },
        ensure_ascii=False,
    )


def parse_tool_result(raw: str) -> ToolEnvelope:
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return ToolEnvelope(ok=True, data=raw, error_code=None, quality_score=1.0, raw=raw)
    if not isinstance(body, dict) or "ok" not in body:
        return ToolEnvelope(ok=True, data=body, error_code=None, quality_score=1.0, raw=raw)
    score = body.get("quality_score")
    if score is not None and not isinstance(score, (int, float)):
        score = None
    return ToolEnvelope(
        ok=bool(body.get("ok")),
        data=body.get("data"),
        error_code=body.get("error_code") if isinstance(body.get("error_code"), str) else None,
        quality_score=float(score) if score is not None else None,
        raw=raw,
    )


def with_quality_hint(raw: str, hint: str) -> str:
    env = parse_tool_result(raw)
    if isinstance(env.data, dict):
        data = {**env.data, "platform_quality_hint": hint}
    else:
        data = {"result": env.data, "platform_quality_hint": hint}
    return success_envelope(
        data,
        quality_score=env.quality_score if env.quality_score is not None else 0.0,
    )
