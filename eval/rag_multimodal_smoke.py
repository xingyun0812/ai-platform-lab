#!/usr/bin/env python3
"""Phase P P2 — RAG 多模态索引 smoke（无 Gateway / Qdrant）。"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
)


async def main() -> int:
    from packages.embedding.models import EmbeddingRegistry
    from packages.embedding.service import EmbeddingService, reset_embedding_service_for_tests
    from packages.platform import configure
    from packages.platform.testing import InMemoryPlatformPort
    from packages.rag.embeddings import embed_rag_chunks
    from packages.rag.multimodal_index import chunk_image_file, load_image_caption

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        img = root / "chart.png"
        img.write_bytes(TINY_PNG)
        (root / "chart.png.caption.txt").write_text("季度销售趋势图", encoding="utf-8")

        assert load_image_caption(img) == "季度销售趋势图"
        chunks = chunk_image_file(
            img,
            source_uri="chart.png",
            kb_id="smoke",
            version=1,
        )
        assert len(chunks) == 1 and chunks[0].modality == "image"

        yaml = """
models:
  - model_id: stub-multimodal
    provider: stub
    dimensions: 16
    modalities: [text, image]
""".strip()
        yaml_path = root / "models.yaml"
        yaml_path.write_text(yaml, encoding="utf-8")
        reset_embedding_service_for_tests()
        reg = EmbeddingRegistry(yaml_path=yaml_path)
        reg.load()
        import packages.embedding.service as svc_mod

        svc_mod._global_service = EmbeddingService(registry=reg)

        rag_root = root / "rag_data"
        rag_root.mkdir()
        (rag_root / "chart.png").write_bytes(TINY_PNG)

        port = InMemoryPlatformPort()
        port.settings.rag_data_root = rag_root
        port.settings.embedding_service_enabled = True
        port.settings.rag_multimodal_embedding_model = "stub-multimodal"
        configure(port)

        vectors = await embed_rag_chunks(chunks)

        assert len(vectors) == 1 and len(vectors[0]) == 16
        reset_embedding_service_for_tests()

    print("OK rag_multimodal_smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
