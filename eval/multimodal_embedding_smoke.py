#!/usr/bin/env python3
"""Phase P — 多模态 Embedding smoke（无 Gateway / LLM Key）。"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


async def main() -> int:
    from packages.embedding.models import EmbeddingRegistry, EmbeddingRequest
    from packages.embedding.service import EmbeddingService

    yaml = """
models:
  - model_id: stub-multimodal
    provider: stub
    dimensions: 16
    modalities: [text, image]
""".strip()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.yaml"
        path.write_text(yaml, encoding="utf-8")
        reg = EmbeddingRegistry(yaml_path=path)
        reg.load()
        svc = EmbeddingService(registry=reg)
        req = EmbeddingRequest(
            model_id="stub-multimodal",
            inputs=[
                {"type": "text", "text": "sales chart"},
                {"type": "image_url", "url": "https://example.com/chart.png"},
            ],
        )
        resp = await svc.embed(req)
    assert len(resp.embeddings) == 2
    assert len(resp.embeddings[0]) == 16
    print("OK multimodal_embedding_smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
