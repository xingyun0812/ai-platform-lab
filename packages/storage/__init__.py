"""对象存储抽象层 — Phase K #33 (GitHub Issue #33 / Roadmap #49)

支持：
- LocalStorageBackend：本地文件系统（开发/测试/兜底）
- S3StorageBackend：AWS S3 及 S3 兼容协议（MinIO 等）
- OSStorageBackend：阿里云 OSS

使用场景：
- RAG 文件上传归档
- 审计日志归档（audit archive）
- Memory 快照持久化

快速上手：
    from packages.storage import init_storage, get_storage, StorageConfig

    init_storage(StorageConfig(backend="local"))
    storage = get_storage()
    await storage.put("myfile.txt", b"hello world")
    data = await storage.get("myfile.txt")
"""

from __future__ import annotations

from packages.storage.backend import (
    LocalStorageBackend,
    StorageBackend,
    StorageConfig,
    StorageObject,
)
from packages.storage.factory import (
    create_backend,
    get_storage,
    init_storage,
    reset_for_tests,
)
from packages.storage.oss import OSStorageBackend
from packages.storage.s3 import S3StorageBackend

__all__ = [
    "StorageBackend",
    "LocalStorageBackend",
    "S3StorageBackend",
    "OSStorageBackend",
    "StorageObject",
    "StorageConfig",
    "create_backend",
    "init_storage",
    "get_storage",
    "reset_for_tests",
]
