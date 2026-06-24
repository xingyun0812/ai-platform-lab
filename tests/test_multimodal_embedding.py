"""Phase P — 多模态 Embedding 单测。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from packages.embedding.models import EmbeddingModel, EmbeddingRegistry, EmbeddingRequest
from packages.embedding.multimodal import (
    MultimodalInputError,
    item_fingerprint,
    items_to_openai_input,
    normalize_item,
    normalize_items,
    validate_modalities,
)
from packages.embedding.providers import StubProvider
from packages.embedding.service import EmbeddingService


def _run(coro):
    return asyncio.run(coro)


class MultimodalNormalizeTests(unittest.TestCase):
    def test_text_string(self) -> None:
        item = normalize_item("hello")
        self.assertEqual(item, {"type": "text", "text": "hello"})

    def test_image_url(self) -> None:
        item = normalize_item({"type": "image_url", "url": "https://example.com/a.png"})
        self.assertEqual(item["type"], "image_url")

    def test_image_base64(self) -> None:
        item = normalize_item(
            {"type": "image_base64", "mime": "image/png", "data": "abc123"}
        )
        self.assertEqual(item["mime"], "image/png")

    def test_fingerprint_stable(self) -> None:
        a = item_fingerprint(normalize_item("x"))
        b = item_fingerprint(normalize_item("x"))
        self.assertEqual(a, b)

    def test_validate_modalities_rejects_image_on_text_only(self) -> None:
        items = normalize_items([{"type": "image_url", "url": "https://x"}])
        with self.assertRaises(MultimodalInputError):
            validate_modalities(items, allowed=["text"])


class MultimodalOpenAIFormatTests(unittest.TestCase):
    def test_text_only_string(self) -> None:
        items = normalize_items(["hello"])
        self.assertEqual(items_to_openai_input(items), "hello")

    def test_mixed_content_array(self) -> None:
        items = normalize_items(
            [
                {"type": "text", "text": "describe"},
                {"type": "image_url", "url": "https://img"},
            ]
        )
        out = items_to_openai_input(items)
        self.assertIsInstance(out, list)
        self.assertEqual(len(out), 2)


class MultimodalServiceTests(unittest.TestCase):
    def test_stub_multimodal_embed(self) -> None:
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
            reg = EmbeddingRegistry(yaml_path=yaml_path)
            reg.load()
            svc = EmbeddingService(registry=reg, cache_max_size=100)
            req = EmbeddingRequest(
                model_id="stub-multimodal",
                inputs=[
                    {"type": "text", "text": "chart"},
                    {"type": "image_url", "url": "https://example.com/chart.png"},
                ],
            )

            async def _go():
                return await svc.embed(req)

            resp = _run(_go())
            self.assertEqual(len(resp.embeddings), 2)
            self.assertEqual(len(resp.embeddings[0]), 8)
            self.assertNotEqual(resp.embeddings[0], resp.embeddings[1])

    def test_cache_hit_multimodal(self) -> None:
        model = EmbeddingModel(
            model_id="m",
            name="m",
            provider="stub",
            dimensions=4,
            modalities=["text", "image"],
        )
        reg = EmbeddingRegistry()
        reg._models["m"] = model
        reg._loaded = True
        svc = EmbeddingService(registry=reg, cache_max_size=10)
        inp = [{"type": "image_url", "url": "https://a"}]

        async def _once():
            return await svc.embed(EmbeddingRequest(model_id="m", inputs=inp))

        r1 = _run(_once())
        r2 = _run(_once())
        self.assertEqual(r1.embeddings[0], r2.embeddings[0])
        self.assertEqual(r2.usage["cached_inputs"], 1)

    def test_stub_provider_image_deterministic(self) -> None:
        model = EmbeddingModel(
            model_id="m",
            name="m",
            provider="stub",
            dimensions=16,
            modalities=["text", "image"],
        )
        items = normalize_items([{"type": "image_base64", "mime": "image/png", "data": "AA"}])

        async def _go():
            return await StubProvider().embed(items, model)

        a = _run(_go())
        b = _run(_go())
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
