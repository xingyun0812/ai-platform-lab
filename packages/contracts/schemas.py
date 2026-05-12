from typing import Any

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: str | None = None


class ChatCompletionRequest(BaseModel):
    """OpenAI Chat Completions 子集；其余字段原样透传给上游。"""

    model: str | None = None
    messages: list[ChatMessage]
    stream: bool | None = False
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None

    model_config = {"extra": "allow"}

    def upstream_payload(self, default_model: str) -> dict[str, Any]:
        data: dict[str, Any] = self.model_dump(exclude_none=True)
        data["model"] = self.model or default_model
        return data
