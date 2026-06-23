#!/usr/bin/env python3
"""对象存储单元测试 — Phase K #33

运行：
    python3 tests/test_storage.py

测试覆盖：
 1.  StorageConfig 默认值
 2.  LocalStorageBackend.put / get
 3.  LocalStorageBackend.exists (true / false)
 4.  LocalStorageBackend.delete
 5.  LocalStorageBackend.list (前缀过滤)
 6.  LocalStorageBackend.put_file / get_file 便捷方法
 7.  LocalStorageBackend 元数据 sidecar
 8.  S3StorageBackend — mock boto3 put_object / get_object / delete_object
 9.  S3StorageBackend — mock boto3 list_objects_v2 / head_object (exists)
10.  OSStorageBackend — mock oss2 put_object / get_object
11.  create_backend 工厂函数 — 返回正确类型
12.  全局单例 init_storage / get_storage / reset_for_tests
13.  create_backend 未知后端 → ValueError
14.  LocalStorageBackend get 不存在对象 → FileNotFoundError
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# 辅助：通过 importlib.util 加载模块，避免触发 pydantic 链
# ---------------------------------------------------------------------------


def _load_module(rel_path: str, mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, REPO_ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# 先加载 backend（无外部依赖）
_backend_mod = _load_module("packages/storage/backend.py", "packages.storage.backend")
StorageBackend = _backend_mod.StorageBackend
LocalStorageBackend = _backend_mod.LocalStorageBackend
StorageConfig = _backend_mod.StorageConfig
StorageObject = _backend_mod.StorageObject

# 加载 factory（依赖 backend）
_factory_mod = _load_module("packages/storage/factory.py", "packages.storage.factory")
create_backend = _factory_mod.create_backend
init_storage = _factory_mod.init_storage
get_storage = _factory_mod.get_storage
reset_for_tests = _factory_mod.reset_for_tests


def _run_async(coro):
    """兼容 Python 3.9 的 async 测试运行。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# 1. StorageConfig 默认值
# ---------------------------------------------------------------------------


def test_storage_config_defaults():
    cfg = StorageConfig()
    assert cfg.backend == "local"
    assert cfg.bucket == "ai-platform-lab"
    assert cfg.prefix == ""
    assert cfg.region == "us-east-1"
    assert cfg.endpoint is None
    assert cfg.access_key is None
    assert cfg.secret_key is None
    print("PASS test_storage_config_defaults")


# ---------------------------------------------------------------------------
# 2. LocalStorageBackend.put / get
# ---------------------------------------------------------------------------


def test_local_put_get():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = StorageConfig(local_root=Path(tmpdir))
        backend = LocalStorageBackend(cfg)

        async def run():
            key = await backend.put("hello.txt", b"hello world")
            assert key == "hello.txt"
            data = await backend.get("hello.txt")
            assert data == b"hello world"

        _run_async(run())
    print("PASS test_local_put_get")


# ---------------------------------------------------------------------------
# 3. LocalStorageBackend.exists
# ---------------------------------------------------------------------------


def test_local_exists():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = StorageConfig(local_root=Path(tmpdir))
        backend = LocalStorageBackend(cfg)

        async def run():
            assert await backend.exists("nonexistent.txt") is False
            await backend.put("exists.txt", b"data")
            assert await backend.exists("exists.txt") is True

        _run_async(run())
    print("PASS test_local_exists")


# ---------------------------------------------------------------------------
# 4. LocalStorageBackend.delete
# ---------------------------------------------------------------------------


def test_local_delete():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = StorageConfig(local_root=Path(tmpdir))
        backend = LocalStorageBackend(cfg)

        async def run():
            await backend.put("todelete.bin", b"\x00\x01\x02")
            ok = await backend.delete("todelete.bin")
            assert ok is True
            assert await backend.exists("todelete.bin") is False
            # 删除不存在的对象返回 False
            ok2 = await backend.delete("todelete.bin")
            assert ok2 is False

        _run_async(run())
    print("PASS test_local_delete")


# ---------------------------------------------------------------------------
# 5. LocalStorageBackend.list（前缀过滤）
# ---------------------------------------------------------------------------


def test_local_list_prefix():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = StorageConfig(local_root=Path(tmpdir))
        backend = LocalStorageBackend(cfg)

        async def run():
            await backend.put("dir/a.txt", b"a")
            await backend.put("dir/b.txt", b"b")
            await backend.put("other/c.txt", b"c")

            all_keys = await backend.list()
            assert len(all_keys) == 3

            dir_keys = await backend.list(prefix="dir/")
            assert len(dir_keys) == 2
            assert all(k.startswith("dir/") for k in dir_keys)

        _run_async(run())
    print("PASS test_local_list_prefix")


# ---------------------------------------------------------------------------
# 6. LocalStorageBackend.put_file / get_file
# ---------------------------------------------------------------------------


def test_local_put_get_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = StorageConfig(local_root=Path(tmpdir))
        backend = LocalStorageBackend(cfg)

        src = Path(tmpdir) / "source.bin"
        src.write_bytes(b"file content 123")

        dest = Path(tmpdir) / "download" / "out.bin"

        async def run():
            result_key = await backend.put_file(src, "uploads/source.bin")
            assert result_key == "uploads/source.bin"

            downloaded = await backend.get_file("uploads/source.bin", dest)
            assert downloaded == dest
            assert dest.read_bytes() == b"file content 123"

        _run_async(run())
    print("PASS test_local_put_get_file")


# ---------------------------------------------------------------------------
# 7. LocalStorageBackend 元数据 sidecar
# ---------------------------------------------------------------------------


def test_local_metadata_sidecar():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = StorageConfig(local_root=Path(tmpdir))
        backend = LocalStorageBackend(cfg)

        async def run():
            await backend.put("meta_test.txt", b"content", metadata={"author": "test", "version": "1"})
            meta = await backend.get_metadata("meta_test.txt")
            assert meta["key"] == "meta_test.txt"
            assert meta["size"] == 7  # len(b"content")
            assert meta["metadata"]["author"] == "test"
            assert meta["metadata"]["version"] == "1"
            assert "last_modified" in meta

        _run_async(run())
    print("PASS test_local_metadata_sidecar")


# ---------------------------------------------------------------------------
# 8. S3StorageBackend — mock boto3 put/get/delete
# ---------------------------------------------------------------------------


def _make_boto3_mock():
    """构建 mock boto3 模块。"""
    boto3_mock = MagicMock()
    client_mock = MagicMock()
    boto3_mock.client.return_value = client_mock

    botocore_mock = types.ModuleType("botocore")
    exceptions_mock = types.ModuleType("botocore.exceptions")

    class ClientError(Exception):
        def __init__(self, error_response, operation_name):
            self.response = error_response
            self.operation_name = operation_name
            super().__init__(str(error_response))

    exceptions_mock.ClientError = ClientError
    botocore_mock.exceptions = exceptions_mock

    return boto3_mock, client_mock, ClientError, botocore_mock


def test_s3_put_get_delete():
    boto3_mock, client_mock, ClientError, botocore_mock = _make_boto3_mock()

    # mock get_object 返回
    body_mock = MagicMock()
    body_mock.read.return_value = b"s3 data"
    client_mock.get_object.return_value = {"Body": body_mock}
    client_mock.delete_object.return_value = {}

    with patch.dict(sys.modules, {"boto3": boto3_mock, "botocore": botocore_mock, "botocore.exceptions": botocore_mock.exceptions}):
        # 重新加载 s3 模块（使用 mock）
        s3_spec = importlib.util.spec_from_file_location(
            "packages.storage.s3_test", REPO_ROOT / "packages/storage/s3.py"
        )
        s3_mod = importlib.util.module_from_spec(s3_spec)
        # 注入 mock backend 依赖
        s3_mod.__spec__ = s3_spec
        sys.modules["packages.storage.s3_test"] = s3_mod
        s3_spec.loader.exec_module(s3_mod)

        cfg = StorageConfig(
            backend="s3",
            bucket="test-bucket",
            access_key="AKID",
            secret_key="SECRET",
        )
        backend = s3_mod.S3StorageBackend(cfg)

        async def run():
            key = await backend.put("test.txt", b"hello s3", metadata={"env": "test"})
            assert key == "test.txt"
            client_mock.put_object.assert_called_once()

            data = await backend.get("test.txt")
            assert data == b"s3 data"

            ok = await backend.delete("test.txt")
            assert ok is True
            client_mock.delete_object.assert_called_once()

        _run_async(run())
    print("PASS test_s3_put_get_delete")


# ---------------------------------------------------------------------------
# 9. S3StorageBackend — list / exists (mock)
# ---------------------------------------------------------------------------


def test_s3_list_exists():
    boto3_mock, client_mock, ClientError, botocore_mock = _make_boto3_mock()

    # mock list_objects_v2 paginator
    paginator_mock = MagicMock()
    page1 = {"Contents": [{"Key": "file1.txt"}, {"Key": "file2.txt"}]}
    paginator_mock.paginate.return_value = [page1]
    client_mock.get_paginator.return_value = paginator_mock

    # mock head_object: 第一次成功，第二次抛 404
    call_count = [0]

    def head_side_effect(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"ContentLength": 100, "ETag": '"abc"', "Metadata": {}}
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")

    client_mock.head_object.side_effect = head_side_effect

    with patch.dict(sys.modules, {"boto3": boto3_mock, "botocore": botocore_mock, "botocore.exceptions": botocore_mock.exceptions}):
        s3_spec = importlib.util.spec_from_file_location(
            "packages.storage.s3_test2", REPO_ROOT / "packages/storage/s3.py"
        )
        s3_mod = importlib.util.module_from_spec(s3_spec)
        sys.modules["packages.storage.s3_test2"] = s3_mod
        s3_spec.loader.exec_module(s3_mod)

        cfg = StorageConfig(backend="s3", bucket="test-bucket", access_key="K", secret_key="S")
        backend = s3_mod.S3StorageBackend(cfg)

        async def run():
            keys = await backend.list()
            assert "file1.txt" in keys
            assert "file2.txt" in keys

            exists1 = await backend.exists("file1.txt")
            assert exists1 is True

            exists2 = await backend.exists("nonexistent.txt")
            assert exists2 is False

        _run_async(run())
    print("PASS test_s3_list_exists")


# ---------------------------------------------------------------------------
# 10. OSStorageBackend — mock oss2 put/get
# ---------------------------------------------------------------------------


def test_oss_put_get():
    oss2_mock = MagicMock()

    # mock Bucket client
    bucket_instance = MagicMock()
    oss2_mock.Bucket.return_value = bucket_instance
    oss2_mock.Auth.return_value = MagicMock()

    # put_object
    bucket_instance.put_object.return_value = MagicMock()

    # get_object
    get_result = MagicMock()
    get_result.read.return_value = b"oss data content"
    bucket_instance.get_object.return_value = get_result

    # ObjectIterator
    obj1 = MagicMock()
    obj1.key = "test/file.txt"
    oss2_mock.ObjectIterator.return_value = [obj1]

    with patch.dict(sys.modules, {"oss2": oss2_mock}):
        oss_spec = importlib.util.spec_from_file_location(
            "packages.storage.oss_test", REPO_ROOT / "packages/storage/oss.py"
        )
        oss_mod = importlib.util.module_from_spec(oss_spec)
        sys.modules["packages.storage.oss_test"] = oss_mod
        oss_spec.loader.exec_module(oss_mod)

        cfg = StorageConfig(
            backend="oss",
            bucket="my-oss-bucket",
            endpoint="https://oss-cn-hangzhou.aliyuncs.com",
            access_key="oss-ak",
            secret_key="oss-sk",
        )
        backend = oss_mod.OSStorageBackend(cfg)

        async def run():
            key = await backend.put("test/file.txt", b"data", metadata={"env": "test"})
            assert key == "test/file.txt"
            bucket_instance.put_object.assert_called_once()

            data = await backend.get("test/file.txt")
            assert data == b"oss data content"

            keys = await backend.list("test/")
            assert "test/file.txt" in keys

        _run_async(run())
    print("PASS test_oss_put_get")


# ---------------------------------------------------------------------------
# 11. create_backend 工厂 — 返回正确类型
# ---------------------------------------------------------------------------


def test_create_backend_factory():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_local = StorageConfig(backend="local", local_root=Path(tmpdir))
        backend = create_backend(cfg_local)
        assert isinstance(backend, LocalStorageBackend)
    print("PASS test_create_backend_factory")


# ---------------------------------------------------------------------------
# 12. 全局单例 init / get / reset
# ---------------------------------------------------------------------------


def test_singleton_lifecycle():
    reset_for_tests()
    assert get_storage() is None

    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = StorageConfig(backend="local", local_root=Path(tmpdir))
        backend = init_storage(cfg)
        assert backend is not None
        assert get_storage() is backend

    reset_for_tests()
    assert get_storage() is None
    print("PASS test_singleton_lifecycle")


# ---------------------------------------------------------------------------
# 13. create_backend 未知后端 → ValueError
# ---------------------------------------------------------------------------


def test_create_backend_unknown():
    cfg = StorageConfig(backend="gcs")
    try:
        create_backend(cfg)
        assert False, "Expected ValueError"
    except ValueError as e:
        assert "gcs" in str(e).lower() or "unknown" in str(e).lower()
    print("PASS test_create_backend_unknown")


# ---------------------------------------------------------------------------
# 14. LocalStorageBackend get 不存在对象 → FileNotFoundError
# ---------------------------------------------------------------------------


def test_local_get_missing_raises():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = StorageConfig(local_root=Path(tmpdir))
        backend = LocalStorageBackend(cfg)

        async def run():
            try:
                await backend.get("does_not_exist.txt")
                assert False, "Expected FileNotFoundError"
            except FileNotFoundError:
                pass

        _run_async(run())
    print("PASS test_local_get_missing_raises")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        test_storage_config_defaults,
        test_local_put_get,
        test_local_exists,
        test_local_delete,
        test_local_list_prefix,
        test_local_put_get_file,
        test_local_metadata_sidecar,
        test_s3_put_get_delete,
        test_s3_list_exists,
        test_oss_put_get,
        test_create_backend_factory,
        test_singleton_lifecycle,
        test_create_backend_unknown,
        test_local_get_missing_raises,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            import traceback
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
