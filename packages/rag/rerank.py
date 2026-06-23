from __future__ import annotations

from packages.contracts.rag_schemas import RetrievedChunk
from packages.rag.rerank_providers import get_rerank_provider


def rerank_chunks(
    query: str,
    chunks: list[RetrievedChunk],
    *,
    top_n: int,
    mode: str = "stub",
    provider_config: dict | None = None,
) -> tuple[list[RetrievedChunk], float]:
    """对检索候选重排序；mode: stub | api | local。"""
    provider = get_rerank_provider(mode, provider_config)
    return provider.rerank(query, chunks, top_n=top_n)


def rerank_provider_name(mode: str, provider_config: dict | None = None) -> str:
    return get_rerank_provider(mode, provider_config).name
