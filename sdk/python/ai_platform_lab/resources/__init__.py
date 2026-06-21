"""Resources package."""
from __future__ import annotations

from ai_platform_lab.resources.agent import AgentResource, AsyncAgentResource
from ai_platform_lab.resources.chat import AsyncChatResource, ChatResource
from ai_platform_lab.resources.embedding import AsyncEmbeddingResource, EmbeddingResource
from ai_platform_lab.resources.memory import AsyncMemoryResource, MemoryResource
from ai_platform_lab.resources.orchestrator import AsyncOrchestratorResource, OrchestratorResource
from ai_platform_lab.resources.rag import AsyncRagResource, RagResource

__all__ = [
    "ChatResource",
    "AsyncChatResource",
    "RagResource",
    "AsyncRagResource",
    "AgentResource",
    "AsyncAgentResource",
    "EmbeddingResource",
    "AsyncEmbeddingResource",
    "MemoryResource",
    "AsyncMemoryResource",
    "OrchestratorResource",
    "AsyncOrchestratorResource",
]
