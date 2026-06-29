"""长记忆摘要服务 — Phase F #31

调用 LLM 将会话历史压缩为长期记忆。
- 输入：messages 列表
- 输出：summary 文本（可入库为 MemoryRecord）

设计：
- 使用独立模型（可配置 AGENT_MEMORY_MODEL 或回退到 default_model）
- prompt 模板从 prompt registry 取（prompt_id="memory_summarize"）
- 失败时回退为简单截断（前 N 字符）
"""

from __future__ import annotations

import logging
import time
from typing import Any

from packages.platform import forward_with_model_router, get_settings

logger = logging.getLogger("ai_platform.memory.summarize")

# 默认摘要 prompt（若 registry 中无 memory_summarize id）
_FALLBACK_SUMMARY_PROMPT = """请将以下对话历史压缩为简洁的长期记忆要点。
- 保留用户的关键偏好、事实、意图
- 去除无关寒暄与冗余
- 输出 3-5 条 bullet，每条不超过 80 字
- 用中文输出

对话历史：
{history}
"""


async def summarize_messages(
    messages: list[dict[str, Any]],
    *,
    tenant_id: str,
    max_input_chars: int = 4000,
) -> str:
    """将 messages 压缩为 summary 文本。

    返回：summary 字符串。失败时返回截断后的 history（保证可用性）。
    """
    if not messages:
        return ""

    # 拼接为历史文本
    history_parts: list[str] = []
    total_chars = 0
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if not isinstance(content, str):
            continue
        line = f"{role}: {content}"
        if total_chars + len(line) > max_input_chars:
            # 截断
            remaining = max_input_chars - total_chars
            if remaining > 0:
                history_parts.append(line[:remaining])
            break
        history_parts.append(line)
        total_chars += len(line)
    history = "\n".join(history_parts)
    if not history:
        return ""

    settings = get_settings()
    if not (settings.llm_api_key or "").strip():
        # 无 Key：返回截断 history 作为兜底
        return history[:500]

    # 取 prompt 模板（优先 registry，回退 fallback）
    prompt_text = _FALLBACK_SUMMARY_PROMPT
    if settings.prompt_registry_enabled:
        try:
            from packages.prompt import get_registry

            reg = get_registry()
            if reg is not None:
                entry = reg.get_active("memory_summarize")
                if entry is not None and entry.version > 0:
                    prompt_text = entry.content
        except Exception as e:
            logger.debug("memory_summarize prompt lookup failed: %s", e)

    # 渲染（registry 用 {{history}}，fallback 用 {history}）
    if "{{history}}" in prompt_text:
        user_prompt = prompt_text.replace("{{history}}", history)
    else:
        user_prompt = prompt_text.replace("{history}", history)

    model = settings.agent_memory_model or settings.default_model
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是记忆压缩助手，只输出关键要点。"},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
    }

    try:
        t0 = time.perf_counter()
        routed = await forward_with_model_router(
            payload,
            requested_model=model,
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        if routed.error and routed.body is None:
            logger.warning("memory summarize upstream error: %s", routed.error)
            return history[:500]
        if routed.body is None or not (200 <= routed.status < 300):
            logger.warning("memory summarize upstream status=%s", routed.status)
            return history[:500]
        # 提取 content
        choices = routed.body.get("choices") or []
        if not choices:
            return history[:500]
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(msg, dict):
            return history[:500]
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            return history[:500]
        logger.info(
            "memory summarize tenant=%s input_chars=%d output_chars=%d latency_ms=%.0f",
            tenant_id,
            len(history),
            len(content),
            latency_ms,
        )
        return content.strip()
    except Exception as e:
        logger.warning("memory summarize failed: %s", e)
        return history[:500]
