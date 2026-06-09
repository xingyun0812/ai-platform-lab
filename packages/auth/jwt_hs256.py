from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def decode_hs256(token: str, secret: str) -> dict[str, Any] | None:
    """最小 HS256 JWT 解析（无 exp 校验扩展）。"""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        header = json.loads(_b64url_decode(parts[0]))
        if header.get("alg") != "HS256":
            return None
        signing_input = f"{parts[0]}.{parts[1]}".encode()
        sig = _b64url_decode(parts[2])
        expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64url_decode(parts[1]))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None
