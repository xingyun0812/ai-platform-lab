#!/usr/bin/env python3
"""Phase P P3 — Python SDK 多模态 embedding smoke（mock HTTP）。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "sdk" / "python"))

from ai_platform_lab import Client  # noqa: E402


def main() -> int:
    embed_body = {
        "model_id": "stub-multimodal",
        "embeddings": [[0.1, 0.2], [0.3, 0.4]],
        "dimensions": 2,
        "usage": {},
        "cached": 0,
    }
    list_body = {"models": [{"model_id": "stub-multimodal", "modalities": ["text", "image"]}]}

    responses = iter(
        [
            httpx.Response(200, json=list_body),
            httpx.Response(200, json=embed_body),
        ]
    )

    with patch.object(httpx.Client, "request", side_effect=lambda *a, **kw: next(responses)):
        with Client("http://127.0.0.1:8000", api_key="k", tenant_id="admin") as c:
            models = c.embedding.list_models()
            assert models[0]["model_id"] == "stub-multimodal"
            resp = c.embedding.create_with_inputs(
                "stub-multimodal",
                [
                    {"type": "text", "text": "chart"},
                    {"type": "image_url", "url": "https://example.com/chart.png"},
                ],
            )
            assert len(resp["embeddings"]) == 2

    print("OK sdk_multimodal_smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
