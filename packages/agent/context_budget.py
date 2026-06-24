from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from packages.agent.session_state import SessionState, flatten_turns, split_turns

SUMMARY_TAG = "[session_summary]"


@dataclass(frozen=True)
class ContextBudgetMeta:
    budget: int
    estimated_tokens: int
    truncated_messages: int
    truncated_tool_results: int
    summary_applied: bool
    keep_recent_turns: int


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_message_tokens(msg: dict[str, Any]) -> int:
    content = msg.get("content")
    if isinstance(content, str):
        return estimate_tokens(content)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return estimate_tokens("".join(parts))
    return 0


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


def truncate_tool_content(content: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(content) <= max_chars:
        return content, False
    marker = "\n...[tool_result_truncated]"
    keep = max(0, max_chars - len(marker))
    return content[:keep] + marker, True


def truncate_tool_messages(
    messages: list[dict[str, Any]],
    *,
    max_chars: int,
) -> tuple[list[dict[str, Any]], int]:
    if max_chars <= 0:
        return messages, 0
    out: list[dict[str, Any]] = []
    truncated = 0
    for msg in messages:
        if msg.get("role") != "tool":
            out.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            out.append(msg)
            continue
        new_content, did = truncate_tool_content(content, max_chars)
        if did:
            truncated += 1
            out.append({**msg, "content": new_content})
        else:
            out.append(msg)
    return out, truncated


def stub_summarize(existing: str | None, messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    if existing:
        parts.append(existing)
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            snippet = content.strip().replace("\n", " ")[:160]
            parts.append(f"{role}: {snippet}")
    joined = " | ".join(parts)
    return joined[:2000]


def maybe_compact_session(
    state: SessionState,
    *,
    every_n_turns: int,
    keep_recent_turns: int,
) -> SessionState:
    if every_n_turns <= 0 or state.turn_count <= 0 or state.turn_count % every_n_turns != 0:
        return state
    turns = split_turns(state.messages)
    if len(turns) <= keep_recent_turns:
        return state
    old_turns = turns[:-keep_recent_turns]
    recent = flatten_turns(turns[-keep_recent_turns:])
    old_flat = flatten_turns(old_turns)
    return SessionState(
        messages=recent,
        summary=stub_summarize(state.summary, old_flat),
        turn_count=state.turn_count,
    )


def drop_oldest_until_budget(
    messages: list[dict[str, Any]],
    *,
    budget: int,
    pinned_prefix: int,
) -> tuple[list[dict[str, Any]], int]:
    working = list(messages)
    dropped = 0
    while len(working) > pinned_prefix and estimate_messages_tokens(working) > budget:
        drop_at = pinned_prefix
        working.pop(drop_at)
        dropped += 1
    return working, dropped


def assemble_llm_messages(
    state: SessionState,
    new_messages: list[dict[str, Any]],
    *,
    budget: int,
    keep_recent_turns: int,
    tool_result_max_chars: int,
) -> tuple[list[dict[str, Any]], ContextBudgetMeta]:
    """budget-aware context assembly：摘要 + 最近 N 轮 + 超预算裁 history。"""
    raw = [*state.messages, *new_messages]
    raw, tool_truncated = truncate_tool_messages(raw, max_chars=tool_result_max_chars)

    turns = split_turns(raw)
    if keep_recent_turns > 0 and len(turns) > keep_recent_turns:
        raw = flatten_turns(turns[-keep_recent_turns:])

    prefix: list[dict[str, Any]] = []
    summary_applied = False
    if state.summary:
        prefix.append(
            {
                "role": "system",
                "content": f"{SUMMARY_TAG} {state.summary}",
            }
        )
        summary_applied = True

    combined = [*prefix, *raw]
    pinned = len(prefix)
    combined, msg_dropped = drop_oldest_until_budget(
        combined,
        budget=budget,
        pinned_prefix=pinned,
    )

    meta = ContextBudgetMeta(
        budget=budget,
        estimated_tokens=estimate_messages_tokens(combined),
        truncated_messages=msg_dropped,
        truncated_tool_results=tool_truncated,
        summary_applied=summary_applied,
        keep_recent_turns=keep_recent_turns,
    )
    return combined, meta


def context_budget_platform_meta(meta: ContextBudgetMeta) -> dict[str, Any]:
    return {
        "budget": meta.budget,
        "estimated_tokens": meta.estimated_tokens,
        "truncated_messages": meta.truncated_messages,
        "truncated_tool_results": meta.truncated_tool_results,
        "summary_applied": meta.summary_applied,
        "keep_recent_turns": meta.keep_recent_turns,
        "remaining": max(0, meta.budget - meta.estimated_tokens),
    }


def context_strategy_platform_meta() -> dict[str, str]:
    """长上下文与 RAG/记忆引用分离策略（Phase O #94）。"""
    return {
        "session_assembly": (
            "assemble_llm_messages：滚动摘要 pinned 前缀 + 最近 N 轮 + 超 budget 删最旧消息"
        ),
        "tool_results": "工具返回先按 tool_result_max_chars 截断，再计入 token budget",
        "memory_injection": (
            "长记忆 retrieve_and_inject_memory 在 assembly 之后，仅用 budget_remaining 注入"
        ),
        "rag_references": (
            "RAG 片段经 get_kb_snippet 等工具写入 tool 消息，与会话摘要/记忆注入通道分离"
        ),
    }
