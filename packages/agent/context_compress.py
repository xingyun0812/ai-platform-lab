"""上下文压缩策略 — Phase F #33

三层压缩策略（按优先级）：
1. **滑窗截断**（已有）：保留最近 N 轮，老对话丢弃
2. **LLM 摘要压缩**（新）：调用 LLM 将老对话压缩为简洁摘要
3. **Token 感知注入**（新）：根据剩余 Token 预算动态决定注入多少 memory

集成点：
- 替换 `maybe_compact_session` 中的 `stub_summarize` 为真实 LLM 调用
- 在 `assemble_llm_messages` 后注入长记忆检索结果

降级链：
- LLM 摘要失败 → 回退 stub_summarize（拼接 snippet）
- 长记忆检索失败 → 跳过注入，不阻塞主流程
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from packages.agent.context_budget import (
    estimate_message_tokens,
    flatten_turns,
    split_turns,
    stub_summarize,
)
from packages.agent.session_state import SessionState

logger = logging.getLogger("ai_platform.agent.context_compress")


@dataclass(frozen=True)
class CompressResult:
    """压缩结果。"""
    messages: list[dict[str, Any]]
    summary: str | None
    summary_source: str  # "llm" | "stub" | "none"
    summary_tokens: int
    compressed_messages: int


async def llm_summarize(
    messages: list[dict[str, Any]],
    *,
    existing_summary: str | None = None,
    tenant_id: str = "",
    max_input_chars: int = 6000,
) -> tuple[str, str]:
    """调用 LLM 压缩 messages 为 summary。

    返回 (summary_text, source)。
    source:
        "llm"  — LLM 调用成功
        "stub" — LLM 失败，回退 stub_summarize
    """
    if not messages:
        return "", "none"

    # 复用 packages.memory.summarize 的实现（同样调 LLM）
    try:
        from packages.memory.summarize import summarize_messages

        # 若已有 summary，作为 history 前置
        history_parts: list[str] = []
        if existing_summary:
            history_parts.append(f"[previous_summary] {existing_summary}")
        # 拼接 messages
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if not isinstance(content, str):
                continue
            history_parts.append(f"{role}: {content}")
        # 截断
        joined = "\n".join(history_parts)
        if len(joined) > max_input_chars:
            joined = joined[:max_input_chars] + "\n...[truncated]"
        # 调用 LLM（messages 接口需要 list[dict]，但 summarize_messages 接受 messages）
        # 构造 fake messages 列表
        fake_messages = [{"role": "system", "content": existing_summary or ""}] + messages
        summary = await summarize_messages(
            fake_messages, tenant_id=tenant_id, max_input_chars=max_input_chars
        )
        if summary and summary.strip():
            return summary.strip(), "llm"
        # 失败：回退 stub
        return stub_summarize(existing_summary, messages), "stub"
    except Exception as e:
        logger.warning("llm_summarize failed: %s", e)
        return stub_summarize(existing_summary, messages), "stub"


async def maybe_compact_with_llm(
    state: SessionState,
    *,
    every_n_turns: int,
    keep_recent_turns: int,
    tenant_id: str = "",
    enable_llm_summary: bool = True,
) -> SessionState:
    """LLM 增强版 maybe_compact_session。

    与 context_budget.maybe_compact_session 行为一致，但用 LLM 替换 stub_summarize。
    """
    if every_n_turns <= 0 or state.turn_count <= 0 or state.turn_count % every_n_turns != 0:
        return state
    turns = split_turns(state.messages)
    if len(turns) <= keep_recent_turns:
        return state
    old_turns = turns[:-keep_recent_turns]
    recent = flatten_turns(turns[-keep_recent_turns:])
    old_flat = flatten_turns(old_turns)

    if enable_llm_summary:
        summary, _source = await llm_summarize(
            old_flat,
            existing_summary=state.summary,
            tenant_id=tenant_id,
        )
    else:
        summary = stub_summarize(state.summary, old_flat)

    return SessionState(
        messages=recent,
        summary=summary or None,
        turn_count=state.turn_count,
    )


@dataclass(frozen=True)
class MemoryInjection:
    """长记忆注入结果。"""
    injected: bool
    memory_count: int
    injected_tokens: int
    memories: list[dict[str, Any]]  # 简化的记忆摘要（用于 platform_meta）
    system_message: dict[str, Any] | None  # 注入的 system 消息


async def retrieve_and_inject_memory(
    *,
    tenant_id: str,
    session_id: str,
    query: str,
    budget_remaining: int,
    top_k: int = 3,
    scope: str = "session",
) -> MemoryInjection:
    """根据当前 query 检索长记忆，构造注入 system 消息。

    Token 感知：仅当剩余 budget > min_inject_tokens 时才注入；
    注入条数动态调整以适应 budget。

    失败时返回 injected=False（不阻塞主流程）。
    """
    if budget_remaining < 200 or not query.strip():
        return MemoryInjection(
            injected=False, memory_count=0, injected_tokens=0,
            memories=[], system_message=None,
        )

    try:
        from packages.memory import get_memory_store

        store = get_memory_store()
        if store is None:
            return MemoryInjection(
                injected=False, memory_count=0, injected_tokens=0,
                memories=[], system_message=None,
            )
        results = await store.search(
            tenant_id=tenant_id,
            scope=scope,
            scope_id=session_id if scope == "session" else tenant_id,
            query=query,
            top_k=top_k,
        )
        if not results:
            return MemoryInjection(
                injected=False, memory_count=0, injected_tokens=0,
                memories=[], system_message=None,
            )
        # 构造注入文本（按 budget 动态裁剪）
        lines: list[str] = []
        used_tokens = 0
        kept: list[dict[str, Any]] = []
        for r in results:
            line = f"- {r.content[:300]}"
            line_tokens = estimate_message_tokens({"role": "system", "content": line})
            if used_tokens + line_tokens > budget_remaining:
                break
            lines.append(line)
            used_tokens += line_tokens
            kept.append({
                "memory_id": r.memory_id,
                "content_preview": r.content[:80],
                "score": r.metadata.get("turn_count") if r.metadata else None,
            })
        if not lines:
            return MemoryInjection(
                injected=False, memory_count=0, injected_tokens=0,
                memories=[], system_message=None,
            )
        text = "以下是过往会话的关键记忆要点，供你参考：\n" + "\n".join(lines)
        return MemoryInjection(
            injected=True,
            memory_count=len(kept),
            injected_tokens=used_tokens,
            memories=kept,
            system_message={"role": "system", "content": text},
        )
    except Exception as e:
        logger.warning("memory inject failed: %s", e)
        return MemoryInjection(
            injected=False, memory_count=0, injected_tokens=0,
            memories=[], system_message=None,
        )


def inject_memory_into_messages(
    messages: list[dict[str, Any]],
    injection: MemoryInjection,
    *,
    position: str = "after_summary",
) -> list[dict[str, Any]]:
    """将注入消息插入 messages。

    position:
        "after_summary" — 在 system summary 消息后插入（默认）
        "prepend"        — 在最前插入
    """
    if not injection.injected or injection.system_message is None:
        return messages
    if position == "prepend":
        return [injection.system_message, *messages]
    # after_summary：找到第一个非 system 消息的位置插入
    out: list[dict[str, Any]] = []
    inserted = False
    for m in messages:
        if not inserted and m.get("role") != "system":
            out.append(injection.system_message)
            inserted = True
        out.append(m)
    if not inserted:
        out.append(injection.system_message)
    return out


def compression_platform_meta(result: CompressResult) -> dict[str, Any]:
    return {
        "summary_source": result.summary_source,
        "summary_tokens": result.summary_tokens,
        "compressed_messages": result.compressed_messages,
    }


def memory_injection_platform_meta(injection: MemoryInjection) -> dict[str, Any]:
    return {
        "injected": injection.injected,
        "memory_count": injection.memory_count,
        "injected_tokens": injection.injected_tokens,
    }
