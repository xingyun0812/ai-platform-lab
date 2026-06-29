"""packages.llm — 上游 LLM HTTP 调用。"""

from packages.llm.chat import forward_chat_completions

__all__ = ["forward_chat_completions"]
