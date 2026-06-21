"""存储后端工厂 + 全局单例 — Phase K #33

create_backend(config) → StorageBackend
init_storage(config) → StorageBackend （设置全局单例）
get_storage() → StorageBackend | None
reset_for_tests()
"""

from __future__ import annotations

import threading

from packages.storage.backend import LocalStorageBackend, StorageBackend, StorageConfig

_lock = threading.RLock()
_instance: StorageBackend | None = None


def create_backend(config: StorageConfig) -> StorageBackend:
    """根据 config.backend 创建对应的存储后端实例。"""
    backend_type = config.backend.lower()
    if backend_type == "local":
        return LocalStorageBackend(config)
    elif backend_type == "s3":
        from packages.storage.s3 import S3StorageBackend
        return S3StorageBackend(config)
    elif backend_type == "oss":
        from packages.storage.oss import OSStorageBackend
        return OSStorageBackend(config)
    else:
        raise ValueError(
            f"Unknown storage backend: {config.backend!r}. "
            "Supported backends: local, s3, oss"
        )


def init_storage(config: StorageConfig) -> StorageBackend:
    """初始化全局存储单例。多次调用会覆盖已有单例。"""
    global _instance
    with _lock:
        _instance = create_backend(config)
        return _instance


def get_storage() -> StorageBackend | None:
    """获取全局存储单例，未初始化时返回 None（优雅降级）。"""
    return _instance


def reset_for_tests() -> None:
    """清除全局单例，用于测试隔离。"""
    global _instance
    with _lock:
        _instance = None
