from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod

import httpx

logger = logging.getLogger("ai_platform.secrets")


class SecretProvider(ABC):
    @abstractmethod
    def get_secret(self, ref: str) -> str:
        raise NotImplementedError


class EnvSecretProvider(SecretProvider):
    """从环境变量读取；ref 可为 `env:VAR` 或路径 `tenants/demo-a/bearer`。"""

    def get_secret(self, ref: str) -> str:
        if ref.startswith("env:"):
            key = ref[4:].strip()
        else:
            key = "SECRET_" + re.sub(r"[^A-Za-z0-9]+", "_", ref).strip("_").upper()
        value = os.environ.get(key, "").strip()
        if not value:
            raise KeyError(f"环境变量未配置: {key} (ref={ref})")
        return value


class VaultSecretProvider(SecretProvider):
    """HashiCorp Vault KV v2：`secret/data/<ref>`。"""

    def __init__(self, *, addr: str, token: str, mount: str = "secret") -> None:
        self._addr = addr.rstrip("/")
        self._token = token
        self._mount = mount.strip("/")

    def get_secret(self, ref: str) -> str:
        url = f"{self._addr}/v1/{self._mount}/data/{ref.lstrip('/')}"
        headers = {"X-Vault-Token": self._token}
        with httpx.Client(timeout=10.0) as client:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            body = r.json()
        data = body.get("data", {}).get("data", {})
        if not isinstance(data, dict):
            raise KeyError(f"Vault 响应格式错误: {ref}")
        for key in ("value", "secret", "token", "bearer"):
            if isinstance(data.get(key), str) and data[key].strip():
                return str(data[key]).strip()
        if len(data) == 1:
            only = next(iter(data.values()))
            if isinstance(only, str) and only.strip():
                return only.strip()
        raise KeyError(f"Vault 路径 {ref} 未找到可用字符串字段")


_provider_singleton: SecretProvider | None = None


def get_secret_provider() -> SecretProvider | None:
    global _provider_singleton
    if _provider_singleton is not None:
        return _provider_singleton
    from packages.platform import get_settings

    settings = get_settings()
    if settings.secrets_provider == "vault":
        if not settings.vault_addr or not settings.vault_token:
            logger.warning("secrets_provider=vault 但 VAULT_ADDR/TOKEN 未配置")
            return None
        _provider_singleton = VaultSecretProvider(
            addr=settings.vault_addr,
            token=settings.vault_token,
            mount=settings.vault_mount,
        )
        return _provider_singleton
    if settings.secrets_provider == "env":
        _provider_singleton = EnvSecretProvider()
        return _provider_singleton
    return None


def resolve_secret(ref: str | None, *, fallback: str | None = None) -> str:
    if ref:
        provider = get_secret_provider()
        if provider is not None:
            try:
                return provider.get_secret(ref)
            except Exception as e:
                logger.warning("secret ref 解析失败 ref=%s: %s", ref, e)
        elif ref.startswith("env:"):
            return EnvSecretProvider().get_secret(ref)
    if fallback and fallback.strip():
        return fallback.strip()
    raise KeyError("密钥未配置")


def reset_secret_provider_for_tests() -> None:
    global _provider_singleton
    _provider_singleton = None
