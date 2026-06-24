"""Phase P P2 — RAG 多模态索引单测。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from packages.rag.chunker import TextChunk
from packages.rag.indexing import plan_incremental_index
from packages.rag.multimodal_index import (
    chunk_image_file,
    image_content_fingerprint,
    is_image_source,
    load_image_caption,
)


# 1x1 PNG
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
)


class MultimodalIndexTests(unittest.TestCase):
    def test_is_image_source(self) -> None:
        self.assertTrue(is_image_source(Path("a.png")))
        self.assertFalse(is_image_source(Path("a.txt")))

    def test_caption_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "chart.png"
            img.write_bytes(TINY_PNG)
            sidecar = Path(tmp) / "chart.png.caption.txt"
            sidecar.write_text("季度销售趋势图", encoding="utf-8")
            self.assertEqual(load_image_caption(img), "季度销售趋势图")

    def test_chunk_image_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "chart.png"
            img.write_bytes(TINY_PNG)
            chunks = chunk_image_file(
                img,
                source_uri="samples/chart.png",
                kb_id="demo",
                version=1,
            )
            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0].modality, "image")
            self.assertEqual(chunks[0].offset, 0)
            self.assertTrue(chunks[0].content_fingerprint)

    def test_incremental_skip_unchanged_image(self) -> None:
        caption = "chart"
        fp = image_content_fingerprint(caption=caption, raw=TINY_PNG)
        chunk = TextChunk(
            chunk_id="demo:1:img:0",
            text=caption,
            source_uri="samples/chart.png",
            offset=0,
            modality="image",
            content_fingerprint=fp,
        )
        existing = [{"offset": 0, "content_hash": fp, "point_id": "p1"}]
        plan = plan_incremental_index([chunk], existing)
        self.assertEqual(plan.skipped_chunks, 1)
        self.assertEqual(plan.chunks_to_embed, [])


class EmbedRagChunksTests(unittest.TestCase):
    def test_embed_image_via_stub_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = Path(tmp) / "models.yaml"
            yaml_path.write_text(
                """
models:
  - model_id: stub-multimodal
    provider: stub
    dimensions: 8
    modalities: [text, image]
""".strip(),
                encoding="utf-8",
            )
            img_path = Path(tmp) / "x.png"
            img_path.write_bytes(TINY_PNG)
            from packages.embedding.models import EmbeddingRegistry
            from packages.embedding.service import EmbeddingService, reset_embedding_service_for_tests
            from packages.rag.embeddings import embed_image_chunk
            from unittest.mock import patch

            reset_embedding_service_for_tests()
            reg = EmbeddingRegistry(yaml_path=yaml_path)
            reg.load()
            svc = EmbeddingService(registry=reg)
            import packages.embedding.service as svc_mod

            svc_mod._global_service = svc

            chunk = chunk_image_file(
                img_path,
                source_uri="x.png",
                kb_id="k",
                version=1,
            )[0]

            from apps.gateway.settings import get_settings

            rag_root = get_settings().rag_data_root
            rag_root.mkdir(parents=True, exist_ok=True)
            (rag_root / "x.png").write_bytes(TINY_PNG)

            async def _go():
                with (
                    patch("packages.rag.embeddings.get_settings") as mock_emb,
                    patch("apps.gateway.rag.paths.get_settings") as mock_path,
                ):
                    for mock_settings in (mock_emb, mock_path):
                        settings = mock_settings.return_value
                        settings.embedding_service_enabled = True
                        settings.rag_multimodal_embedding_model = "stub-multimodal"
                        settings.rag_data_root = rag_root
                    return await embed_image_chunk(chunk)

            vec = asyncio.run(_go())
            self.assertEqual(len(vec), 8)
            (rag_root / "x.png").unlink(missing_ok=True)
            reset_embedding_service_for_tests()


if __name__ == "__main__":
    unittest.main()
