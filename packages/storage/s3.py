"""AWS S3 存储后端 — Phase K #33

使用 boto3（同步 SDK）通过 asyncio.to_thread 封装为异步接口。
boto3 为可选依赖，未安装时抛出清晰错误。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from packages.storage.backend import StorageBackend, StorageConfig, StorageObject


def _import_boto3():
    try:
        import boto3
        from botocore.exceptions import ClientError
        return boto3, ClientError
    except ImportError as e:
        raise ImportError(
            "boto3 is not installed. Run: pip install boto3\n"
            f"Original error: {e}"
        ) from e


class S3StorageBackend(StorageBackend):
    """AWS S3（或兼容 S3 协议的）对象存储后端。"""

    def __init__(self, config: StorageConfig) -> None:
        self._config = config
        boto3, _ = _import_boto3()
        kwargs: dict[str, Any] = {
            "region_name": config.region,
        }
        if config.endpoint:
            kwargs["endpoint_url"] = config.endpoint
        if config.access_key and config.secret_key:
            kwargs["aws_access_key_id"] = config.access_key
            kwargs["aws_secret_access_key"] = config.secret_key
        self._client = boto3.client("s3", **kwargs)
        self._bucket = config.bucket
        self._prefix = config.prefix

    def _full_key(self, key: str) -> str:
        if self._prefix:
            return f"{self._prefix.rstrip('/')}/{key.lstrip('/')}"
        return key.lstrip("/")

    async def put(self, key: str, data: bytes, metadata: dict | None = None) -> str:
        full_key = self._full_key(key)

        def _put():
            kwargs: dict[str, Any] = {
                "Bucket": self._bucket,
                "Key": full_key,
                "Body": data,
            }
            if metadata:
                kwargs["Metadata"] = {k: str(v) for k, v in metadata.items()}
            self._client.put_object(**kwargs)
            return full_key

        return await asyncio.to_thread(_put)

    async def get(self, key: str) -> bytes:
        full_key = self._full_key(key)
        _, ClientError = _import_boto3()

        def _get():
            try:
                resp = self._client.get_object(Bucket=self._bucket, Key=full_key)
                return resp["Body"].read()
            except ClientError as e:
                code = e.response["Error"]["Code"]
                if code in ("NoSuchKey", "404"):
                    raise FileNotFoundError(f"S3 object not found: {full_key}") from e
                raise

        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> bool:
        full_key = self._full_key(key)

        def _delete():
            self._client.delete_object(Bucket=self._bucket, Key=full_key)
            return True

        return await asyncio.to_thread(_delete)

    async def list(self, prefix: str = "") -> list[str]:
        list_prefix = self._full_key(prefix) if prefix else (self._prefix or "")

        def _list():
            results: list[str] = []
            paginator = self._client.get_paginator("list_objects_v2")
            kwargs: dict[str, Any] = {"Bucket": self._bucket}
            if list_prefix:
                kwargs["Prefix"] = list_prefix
            for page in paginator.paginate(**kwargs):
                for obj in page.get("Contents", []):
                    results.append(obj["Key"])
            return results

        return await asyncio.to_thread(_list)

    async def exists(self, key: str) -> bool:
        full_key = self._full_key(key)
        _, ClientError = _import_boto3()

        def _exists():
            try:
                self._client.head_object(Bucket=self._bucket, Key=full_key)
                return True
            except ClientError as e:
                code = e.response["Error"]["Code"]
                if code in ("404", "NoSuchKey"):
                    return False
                raise

        return await asyncio.to_thread(_exists)

    async def get_metadata(self, key: str) -> dict:
        full_key = self._full_key(key)
        _, ClientError = _import_boto3()

        def _meta():
            try:
                resp = self._client.head_object(Bucket=self._bucket, Key=full_key)
                return {
                    "key": key,
                    "size": resp.get("ContentLength", 0),
                    "last_modified": resp["LastModified"].timestamp() if "LastModified" in resp else time.time(),
                    "metadata": resp.get("Metadata", {}),
                    "etag": resp.get("ETag", "").strip('"'),
                }
            except ClientError as e:
                code = e.response["Error"]["Code"]
                if code in ("404", "NoSuchKey"):
                    raise FileNotFoundError(f"S3 object not found: {full_key}") from e
                raise

        return await asyncio.to_thread(_meta)

    def presign_get(self, key: str, expires: int = 3600) -> str:
        """生成预签名 GET URL（同步，通常在路由层调用）。"""
        full_key = self._full_key(key)
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": full_key},
            ExpiresIn=expires,
        )
