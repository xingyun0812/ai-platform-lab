from __future__ import annotations

from packages.agent.tool_envelope import ToolEnvelope, parse_tool_result

QUALITY_HINT = (
    "platform_quality_hint: 上次工具结果质量偏低，请换检索词、换工具，或明确告知用户证据不足。"
)


def assess_tool_output(
    tool_name: str,
    raw: str,
    *,
    min_score: float,
) -> tuple[ToolEnvelope, str]:
    """返回 (envelope, quality_gate)：passed | low_quality | skipped | failed。"""
    env = parse_tool_result(raw)
    if not env.ok:
        return env, "failed"

    if tool_name == "get_kb_snippet":
        data = env.data if isinstance(env.data, dict) else {}
        snippets = data.get("snippets")
        if not isinstance(snippets, list) or not snippets:
            return env, "low_quality"
        score = env.quality_score
        if score is None:
            scores = [s.get("score") for s in snippets if isinstance(s, dict)]
            nums = [float(s) for s in scores if isinstance(s, (int, float))]
            score = max(nums) if nums else 0.0
        if score < min_score:
            return env, "low_quality"
        return env, "passed"

    if env.quality_score is not None and env.quality_score < min_score:
        return env, "low_quality"

    return env, "passed"
