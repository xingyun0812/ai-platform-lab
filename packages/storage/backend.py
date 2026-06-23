"""对象存储抽象层 — 后端基类 + 本地文件系统实现

StorageBackend ABC：定义统一存储接口
LocalStorageBackend：基于本地文件系统，元数据通过 .meta.json sidecar 文件存储
StorageObject：对象元信息数据类
StorageConfig：存储配置数据类
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class StorageObject:
    """对象元信息。"""
    key: str
    size: int
    last_modified: float
    metadata: dict = field(default_factory=dict)
    etag: str | None = None


@dataclass
class StorageConfig:
    """存储后端配置。"""
    backend: str = "local"           # "local" | "s3" | "oss"
    bucket: str = "ai-platform-lab"
    prefix: str = ""
    region: str = "us-east-1"
    endpoint: str | None = None
    access_key: str | None = None
    secret_key: str | None = None
    local_root: Path = field(default_factory=lambda: Path("data") / "storage")


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------


class StorageBackend:
    """对象存储统一接口（抽象基类）。"""

    async def put(self, key: str, data: bytes, metadata: dict | None = None) -> str:
        """上传对象，返回完整 key 或 URL。"""
        raise NotImplementedError

    async def get(self, key: str) -> bytes:
        """下载对象，返回字节数据。"""
        raise NotImplementedError

    async def delete(self, key: str) -> bool:
        """删除对象，返回是否成功。"""
        raise NotImplementedError

    async def list(self, prefix: str = "") -> list[str]:
        """列出对象 key 列表（可选前缀过滤）。"""
        raise NotImplementedError

    async def exists(self, key: str) -> bool:
        """检查对象是否存在。"""
        raise NotImplementedError

    async def get_metadata(self, key: str) -> dict:
        """获取对象元数据。"""
        raise NotImplementedError

    async def put_file(self, local_path: Path, key: str) -> str:
        """从本地文件上传，便捷封装。"""
        data = local_path.read_bytes()
        return await self.put(key, data)

    async def get_file(self, key: str, local_path: Path) -> Path:
        """下载到本地文件，便捷封装。"""
        data = await self.get(key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(data)
        return local_path


# ---------------------------------------------------------------------------
# 本地文件系统后端
# ---------------------------------------------------------------------------


class LocalStorageBackend(StorageBackend):
    """本地文件系统存储后端。

    存储路径：local_root / bucket / prefix / key
    元数据以同名 .meta.json sidecar 文件存储。
    """

    def __init__(self, config: StorageConfig) -> None:
        self._config = config
        self._lock = threading.RLock()
        # 根目录 = local_root / bucket
        self._root = Path(config.local_root) / config.bucket
        if config.prefix:
            self._root = self._root / config.prefix
        self._root.mkdir(parents=True, exist_ok=True)

    def _object_path(self, key: str) -> Path:
        """对象文件路径（安全处理 key 中的路径分隔符）。"""
        # 防止路径穿越
        safe_key = key.lstrip("/")
        return self._root / safe_key

    def _meta_path(self, key: str) -> Path:
        obj_path = self._object_path(key)
        return obj_path.parent / (obj_path.name + ".meta.json")

    def _ensure_parent(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

    async def put(self, key: str, data: bytes, metadata: dict | None = None) -> str:
        def _write():
            with self._lock:
                obj_path = self._object_path(key)
                self._ensure_parent(obj_path)
                obj_path.write_bytes(data)
                meta = {
                    "key": key,
                    "size": len(data),
                    "last_modified": time.time(),
                    "metadata": metadata or {},
                    "etag": None,
                }
                meta_path = self._meta_path(key)
                meta_path.write_text(json.dumps(meta, ensure_ascii=False))
                return key

        return await asyncio.to_thread(_write)

    async def get(self, key: str) -> bytes:
        def _read():
            with self._lock:
                obj_path = self._object_path(key)
                if not obj_path.exists():
                    raise FileNotFoundError(f"Object not found: {key}")
                return obj_path.read_bytes()

        return await asyncio.to_thread(_read)

    async def delete(self, key: str) -> bool:
        def _delete():
            with self._lock:
                obj_path = self._object_path(key)
                if not obj_path.exists():
                    return False
                obj_path.unlink()
                meta_path = self._meta_path(key)
                if meta_path.exists():
                    meta_path.unlink()
                return True

        return await asyncio.to_thread(_delete)

    async def list(self, prefix: str = "") -> list[str]:
        def _list():
            with self._lock:
                results: list[str] = []
                str(self._root)
                for p in self._root.rglob("*"):
                    if p.is_file() and not p.name.endswith(".meta.json"):
                        rel = str(p.relative_to(self._root))
                        if not prefix or rel.startswith(prefix):
                            results.append(rel)
                return sorted(results)

        return await asyncio.to_thread(_list)

    async def exists(self, key: str) -> bool:
        def _exists():
            with self._lock:
                return self._object_path(key).exists()

        return await asyncio.to_thread(_exists)

    async def get_metadata(self, key: str) -> dict:
        def _meta():
            with self._lock:
                meta_path = self._meta_path(key)
                if not meta_path.exists():
                    obj_path = self._object_path(key)
                    if not obj_path.exists():
                        raise FileNotFoundError(f"Object not found: {key}")
                    stat = obj_path.stat()
                    return {
                        "key": key,
                        "size": stat.st_size,
                        "last_modified": stat.st_mtime,
                        "metadata": {},
                        "etag": None,
                    }
                return json.loads(meta_path.read_text())

        return await asyncio.to_thread(_meta)
