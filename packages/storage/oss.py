"""阿里云 OSS 存储后端 — Phase K #33

使用 oss2（同步 SDK）通过 asyncio.to_thread 封装为异步接口。
oss2 为可选依赖，未安装时抛出清晰错误。
"""

from __future__ import annotations

import asyncio
import time

from packages.storage.backend import StorageBackend, StorageConfig


def _import_oss2():
    try:
        import oss2
        return oss2
    except ImportError as e:
        raise ImportError(
            "oss2 is not installed. Run: pip install oss2\n"
            f"Original error: {e}"
        ) from e


class OSStorageBackend(StorageBackend):
    """阿里云 OSS 对象存储后端。

    endpoint 格式示例：https://oss-cn-hangzhou.aliyuncs.com
    """

    def __init__(self, config: StorageConfig) -> None:
        self._config = config
        oss2 = _import_oss2()
        if not config.access_key or not config.secret_key:
            raise ValueError("OSS backend requires access_key and secret_key")
        if not config.endpoint:
            raise ValueError("OSS backend requires endpoint (e.g. https://oss-cn-hangzhou.aliyuncs.com)")
        auth = oss2.Auth(config.access_key, config.secret_key)
        self._bucket_client = oss2.Bucket(auth, config.endpoint, config.bucket)
        self._bucket_name = config.bucket
        self._prefix = config.prefix
        self._oss2 = oss2

    def _full_key(self, key: str) -> str:
        if self._prefix:
            return f"{self._prefix.rstrip('/')}/{key.lstrip('/')}"
        return key.lstrip("/")

    async def put(self, key: str, data: bytes, metadata: dict | None = None) -> str:
        full_key = self._full_key(key)

        def _put():
            headers: dict[str, str] = {}
            if metadata:
                for k, v in metadata.items():
                    headers[f"x-oss-meta-{k}"] = str(v)
            self._bucket_client.put_object(full_key, data, headers=headers or None)
            return full_key

        return await asyncio.to_thread(_put)

    async def get(self, key: str) -> bytes:
        full_key = self._full_key(key)

        def _get():
            result = self._bucket_client.get_object(full_key)
            return result.read()

        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> bool:
        full_key = self._full_key(key)

        def _delete():
            self._bucket_client.delete_object(full_key)
            return True

        return await asyncio.to_thread(_delete)

    async def list(self, prefix: str = "") -> list[str]:
        list_prefix = self._full_key(prefix) if prefix else (self._prefix or "")
        oss2 = self._oss2

        def _list():
            results: list[str] = []
            for obj in oss2.ObjectIterator(self._bucket_client, prefix=list_prefix):
                results.append(obj.key)
            return results

        return await asyncio.to_thread(_list)

    async def exists(self, key: str) -> bool:
        full_key = self._full_key(key)

        def _exists():
            return self._bucket_client.object_exists(full_key)

        return await asyncio.to_thread(_exists)

    async def get_metadata(self, key: str) -> dict:
        full_key = self._full_key(key)

        def _meta():
            meta = self._bucket_client.head_object(full_key)
            headers = meta.headers
            user_meta: dict[str, str] = {}
            for k, v in headers.items():
                lower_k = k.lower()
                if lower_k.startswith("x-oss-meta-"):
                    user_meta[lower_k[len("x-oss-meta-"):]] = v
            return {
                "key": key,
                "size": int(headers.get("Content-Length", 0)),
                "last_modified": time.time(),
                "metadata": user_meta,
                "etag": headers.get("ETag", "").strip('"'),
            }

        return await asyncio.to_thread(_meta)

    def presign_get(self, key: str, expires: int = 3600) -> str:
        """生成预签名 GET URL（同步）。"""
        full_key = self._full_key(key)
        return self._bucket_client.sign_url("GET", full_key, expires)
