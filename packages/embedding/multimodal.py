"""多模态 Embedding 输入归一化（Phase P）。"""

from __future__ import annotations

import hashlib
from typing import Any

SUPPORTED_INPUT_TYPES = frozenset({"text", "image_url", "image_base64"})


class MultimodalInputError(ValueError):
    """无效多模态 embedding 输入。"""


def normalize_item(raw: Any) -> dict[str, Any]:
    """将 str 或 dict 归一化为 canonical input item。"""
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise MultimodalInputError("text input must be non-empty")
        return {"type": "text", "text": text}

    if not isinstance(raw, dict):
        raise MultimodalInputError("input must be str or dict")

    item_type = str(raw.get("type") or "text").strip().lower()
    if item_type == "text":
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise MultimodalInputError("text input requires non-empty text")
        return {"type": "text", "text": text.strip()}

    if item_type == "image_url":
        url = raw.get("url")
        if not isinstance(url, str) or not url.strip():
            raise MultimodalInputError("image_url requires url")
        return {"type": "image_url", "url": url.strip()}

    if item_type == "image_base64":
        data = raw.get("data")
        mime = raw.get("mime") or raw.get("mime_type") or "image/png"
        if not isinstance(data, str) or not data.strip():
            raise MultimodalInputError("image_base64 requires data")
        if not isinstance(mime, str) or not mime.strip():
            raise MultimodalInputError("image_base64 requires mime")
        return {
            "type": "image_base64",
            "mime": mime.strip(),
            "data": data.strip(),
        }

    raise MultimodalInputError(f"unsupported input type: {item_type}")


def normalize_items(raw_items: list[Any]) -> list[dict[str, Any]]:
    if not raw_items:
        raise MultimodalInputError("inputs must be non-empty")
    return [normalize_item(item) for item in raw_items]


def item_modality(item: dict[str, Any]) -> str:
    if item["type"] == "text":
        return "text"
    return "image"


def validate_modalities(
    items: list[dict[str, Any]],
    *,
    allowed: list[str] | tuple[str, ...],
) -> None:
    allowed_set = {m.strip().lower() for m in allowed if m}
    if not allowed_set:
        allowed_set = {"text"}
    for item in items:
        mod = item_modality(item)
        if mod not in allowed_set:
            raise MultimodalInputError(
                f"modality {mod!r} not allowed; model supports {sorted(allowed_set)}"
            )


def item_fingerprint(item: dict[str, Any]) -> str:
    """确定性指纹，用于缓存与 stub 向量。"""
    if item["type"] == "text":
        return f"text:{item['text']}"
    if item["type"] == "image_url":
        return f"image_url:{item['url']}"
    digest = hashlib.sha256(item["data"].encode("utf-8")).hexdigest()[:32]
    return f"image_base64:{item['mime']}:{digest}"


def items_fingerprint(items: list[dict[str, Any]]) -> str:
    return "|".join(item_fingerprint(i) for i in items)


def item_to_embed_text(item: dict[str, Any]) -> str:
    """将多模态 item 降级为文本，供仅支持 text 的上游 embedding API。"""
    if item["type"] == "text":
        return item["text"]
    if item["type"] == "image_url":
        return f"[image_url:{item['url']}]"
    digest = hashlib.sha256(item["data"].encode("utf-8")).hexdigest()[:16]
    return f"[image_base64:{item['mime']}:{digest}]"


def items_to_openai_input(items: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """OpenAI /v1/embeddings：单模态文本走 string；含图走 content 数组。"""
    has_image = any(item_modality(i) == "image" for i in items)
    if not has_image and len(items) == 1 and items[0]["type"] == "text":
        return items[0]["text"]
    if not has_image:
        return "\n".join(item_to_embed_text(i) for i in items)

    content: list[dict[str, Any]] = []
    for item in items:
        if item["type"] == "text":
            content.append({"type": "text", "text": item["text"]})
        elif item["type"] == "image_url":
            content.append({"type": "image_url", "image_url": {"url": item["url"]}})
        else:
            data_url = f"data:{item['mime']};base64,{item['data']}"
            content.append({"type": "image_url", "image_url": {"url": data_url}})
    return content


def parse_modalities(raw: Any) -> list[str]:
    if raw is None:
        return ["text"]
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
        return parts or ["text"]
    if isinstance(raw, list):
        parts = [str(p).strip().lower() for p in raw if str(p).strip()]
        return parts or ["text"]
    return ["text"]
