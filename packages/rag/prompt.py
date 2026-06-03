from __future__ import annotations

from pathlib import Path

from packages.contracts.rag_schemas import RetrievedChunk


def load_prompt_template(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"RAG prompt 模板不存在: {path}")
    return path.read_text(encoding="utf-8")


def build_context_block(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "（无）"
    parts: list[str] = []
    for i, c in enumerate(chunks, start=1):
        parts.append(
            f"[{i}] chunk_id={c.chunk_id} source={c.source_uri} score={c.score:.4f}\n{c.text}"
        )
    return "\n\n".join(parts)


def render_rag_prompt(template: str, *, context: str, query: str) -> str:
    if "{context}" not in template or "{query}" not in template:
        raise ValueError("prompt 模板须包含 {context} 与 {query} 占位符")
    return template.replace("{context}", context).replace("{query}", query)
