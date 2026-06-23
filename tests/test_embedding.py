#!/usr/bin/env python3
"""Embedding 服务单元测试 — Issue #35

运行：
    python3 tests/test_embedding.py

注意：通过 importlib.util 加载模块，避免触发 packages.agent.__init__ 的 pydantic 链
（Python 3.9 兼容）。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# --------------------------------------------------------------------- #
# importlib 加载模块（Python 3.9 兼容）
# --------------------------------------------------------------------- #

def _load_module(name: str, rel_path: str):
    """通过 importlib 加载模块并注册到 sys.modules。"""
    path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# 按依赖顺序加载
_models_mod = _load_module("packages.embedding.models", "packages/embedding/models.py")
_providers_mod = _load_module("packages.embedding.providers", "packages/embedding/providers.py")
_service_mod = _load_module("packages.embedding.service", "packages/embedding/service.py")

EmbeddingModel = _models_mod.EmbeddingModel
EmbeddingRequest = _models_mod.EmbeddingRequest
EmbeddingResponse = _models_mod.EmbeddingResponse
EmbeddingRegistry = _models_mod.EmbeddingRegistry
init_registry = _models_mod.init_registry
get_registry = _models_mod.get_registry
reset_for_tests = _models_mod.reset_for_tests

EmbeddingProvider = _providers_mod.EmbeddingProvider
StubProvider = _providers_mod.StubProvider
OpenAIProvider = _providers_mod.OpenAIProvider
provider_factory = _providers_mod.provider_factory

EmbeddingService = _service_mod.EmbeddingService
init_embedding_service = _service_mod.init_embedding_service
get_embedding_service = _service_mod.get_embedding_service
reset_embedding_service_for_tests = _service_mod.reset_embedding_service_for_tests


# --------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------- #

def _run_async(coro):
    """兼容 Python 3.9 的 async 测试运行器。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_model(
    model_id: str = "test-model",
    provider: str = "stub",
    dimensions: int = 8,
) -> EmbeddingModel:
    return EmbeddingModel(
        model_id=model_id,
        name=model_id,
        provider=provider,
        dimensions=dimensions,
    )


def _make_registry(models=None) -> EmbeddingRegistry:
    reg = EmbeddingRegistry()
    reg.load()  # loads empty (no yaml_path)
    for m in (models or []):
        reg.register_model(m)
    return reg


# --------------------------------------------------------------------- #
# Test 1: EmbeddingModel dataclass fields
# --------------------------------------------------------------------- #

def test_embedding_model_fields():
    m = EmbeddingModel(
        model_id="text-embedding-3-small",
        name="Small Embedding",
        provider="openai",
        dimensions=1536,
        max_input_tokens=8192,
    )
    assert m.model_id == "text-embedding-3-small"
    assert m.name == "Small Embedding"
    assert m.provider == "openai"
    assert m.dimensions == 1536
    assert m.max_input_tokens == 8192
    assert isinstance(m.created_at, float)
    assert isinstance(m.metadata, dict)
    d = m.to_dict()
    assert d["model_id"] == "text-embedding-3-small"
    assert d["dimensions"] == 1536
    print("PASS test_embedding_model_fields")


# --------------------------------------------------------------------- #
# Test 2: EmbeddingRegistry register / get / list
# --------------------------------------------------------------------- #

def test_registry_register_get_list():
    reset_for_tests()
    reg = _make_registry()
    m1 = _make_model("model-a")
    m2 = _make_model("model-b")
    reg.register_model(m1)
    reg.register_model(m2)
    assert reg.get_model("model-a") is m1
    assert reg.get_model("model-b") is m2
    assert reg.get_model("nonexistent") is None
    models = reg.list_models()
    assert len(models) == 2
    # sorted by model_id
    assert models[0].model_id == "model-a"
    assert models[1].model_id == "model-b"
    print("PASS test_registry_register_get_list")


# --------------------------------------------------------------------- #
# Test 3: EmbeddingRegistry remove
# --------------------------------------------------------------------- #

def test_registry_remove():
    reset_for_tests()
    reg = _make_registry([_make_model("del-me")])
    assert reg.get_model("del-me") is not None
    ok = reg.remove_model("del-me")
    assert ok is True
    assert reg.get_model("del-me") is None
    # 重复删除返回 False
    assert reg.remove_model("del-me") is False
    print("PASS test_registry_remove")


# --------------------------------------------------------------------- #
# Test 4: EmbeddingRegistry stats
# --------------------------------------------------------------------- #

def test_registry_stats():
    reset_for_tests()
    reg = _make_registry([
        _make_model("m1", provider="stub"),
        _make_model("m2", provider="openai"),
        _make_model("m3", provider="stub"),
    ])
    stats = reg.stats()
    assert stats["total_models"] == 3
    assert stats["by_provider"]["stub"] == 2
    assert stats["by_provider"]["openai"] == 1
    print("PASS test_registry_stats")


# --------------------------------------------------------------------- #
# Test 5: StubProvider 确定性输出 + 维度正确
# --------------------------------------------------------------------- #

def test_stub_provider_deterministic():
    provider = StubProvider()
    model = _make_model(dimensions=16)

    async def run():
        texts = ["hello", "world", "hello"]
        results = await provider.embed(texts, model)
        assert len(results) == 3
        assert len(results[0]) == 16
        assert len(results[1]) == 16
        # 同一文本输出相同向量（确定性）
        assert results[0] == results[2]
        # 不同文本输出不同向量
        assert results[0] != results[1]
        return results

    _run_async(run())
    print("PASS test_stub_provider_deterministic")


# --------------------------------------------------------------------- #
# Test 6: StubProvider 向量归一化（单位向量）
# --------------------------------------------------------------------- #

def test_stub_provider_normalized():
    provider = StubProvider()
    model = _make_model(dimensions=32)

    async def run():
        results = await provider.embed(["test normalization"], model)
        vec = results[0]
        norm = sum(x * x for x in vec) ** 0.5
        # 应接近 1.0（归一化）
        assert abs(norm - 1.0) < 1e-5, f"norm={norm} not ~1.0"

    _run_async(run())
    print("PASS test_stub_provider_normalized")


# --------------------------------------------------------------------- #
# Test 7: provider_factory 返回 StubProvider 当 provider=="stub"
# --------------------------------------------------------------------- #

def test_provider_factory_stub():
    model = _make_model(provider="stub")
    p = provider_factory(model)
    assert isinstance(p, StubProvider)
    print("PASS test_provider_factory_stub")


# --------------------------------------------------------------------- #
# Test 8: provider_factory 无 API key 时降级到 StubProvider
# --------------------------------------------------------------------- #

def test_provider_factory_openai_no_key():
    orig = os.environ.pop("LLM_API_KEY", None)
    try:
        model = _make_model(provider="openai")
        p = provider_factory(model)
        # 无 key → 降级
        assert isinstance(p, StubProvider)
    finally:
        if orig is not None:
            os.environ["LLM_API_KEY"] = orig
    print("PASS test_provider_factory_openai_no_key")


# --------------------------------------------------------------------- #
# Test 9: EmbeddingService embed + 缓存命中
# --------------------------------------------------------------------- #

def test_service_embed_cache_hit():
    reset_embedding_service_for_tests()
    reg = _make_registry([_make_model("stub-8d", dimensions=8)])
    svc = EmbeddingService(registry=reg, cache_max_size=100)

    async def run():
        req = EmbeddingRequest(model_id="stub-8d", texts=["hello", "world"])
        resp1 = await svc.embed(req)
        assert len(resp1.embeddings) == 2
        assert resp1.dimensions == 8
        assert resp1.usage["total_texts"] == 2
        assert resp1.usage["cached_texts"] == 0
        assert resp1.usage["computed_texts"] == 2
        assert resp1.cached is False

        # 第二次 — 全部命中缓存
        resp2 = await svc.embed(req)
        assert resp2.cached is True
        assert resp2.usage["cached_texts"] == 2
        assert resp2.usage["computed_texts"] == 0
        # 向量内容一致
        assert resp2.embeddings[0] == resp1.embeddings[0]

    _run_async(run())
    print("PASS test_service_embed_cache_hit")


# --------------------------------------------------------------------- #
# Test 10: embed_one 便捷方法
# --------------------------------------------------------------------- #

def test_service_embed_one():
    reset_embedding_service_for_tests()
    reg = _make_registry([_make_model("m1", dimensions=4)])
    svc = EmbeddingService(registry=reg)

    async def run():
        vec = await svc.embed_one("m1", "single text")
        assert isinstance(vec, list)
        assert len(vec) == 4

    _run_async(run())
    print("PASS test_service_embed_one")


# --------------------------------------------------------------------- #
# Test 11: cache_stats + clear_cache
# --------------------------------------------------------------------- #

def test_service_cache_stats_clear():
    reset_embedding_service_for_tests()
    reg = _make_registry([_make_model("m-cache", dimensions=4)])
    svc = EmbeddingService(registry=reg, cache_max_size=50)

    async def run():
        stats0 = svc.cache_stats()
        assert stats0["size"] == 0
        assert stats0["hits"] == 0
        assert stats0["misses"] == 0

        await svc.embed_one("m-cache", "text1")
        await svc.embed_one("m-cache", "text1")  # cache hit

        stats1 = svc.cache_stats()
        assert stats1["size"] == 1
        assert stats1["hits"] == 1
        assert stats1["misses"] == 1

        cleared = svc.clear_cache()
        assert cleared == 1

        stats2 = svc.cache_stats()
        assert stats2["size"] == 0
        assert stats2["hits"] == 0

    _run_async(run())
    print("PASS test_service_cache_stats_clear")


# --------------------------------------------------------------------- #
# Test 12: EmbeddingService 模型不存在时抛 ValueError
# --------------------------------------------------------------------- #

def test_service_unknown_model():
    reset_embedding_service_for_tests()
    reg = _make_registry()
    svc = EmbeddingService(registry=reg)

    async def run():
        req = EmbeddingRequest(model_id="nonexistent", texts=["hello"])
        try:
            await svc.embed(req)
            assert False, "expected ValueError"
        except ValueError as e:
            assert "nonexistent" in str(e)

    _run_async(run())
    print("PASS test_service_unknown_model")


# --------------------------------------------------------------------- #
# Test 13: 全局单例 init / get / reset
# --------------------------------------------------------------------- #

def test_global_singleton():
    reset_embedding_service_for_tests()
    assert get_embedding_service() is None

    with tempfile.TemporaryDirectory():
        svc = init_embedding_service(
            registry_yaml_path=None,
            registry_overrides_path=None,
        )
        assert svc is not None
        assert get_embedding_service() is svc

        # 再次 init 会覆盖
        svc2 = init_embedding_service()
        assert get_embedding_service() is svc2

        reset_embedding_service_for_tests()
        assert get_embedding_service() is None

    print("PASS test_global_singleton")


# --------------------------------------------------------------------- #
# Test 14: YAML 加载
# --------------------------------------------------------------------- #

def test_registry_yaml_load():
    reset_for_tests()
    yaml_content = """
models:
  - model_id: yaml-model-1
    name: YAML Model 1
    provider: stub
    dimensions: 64
  - model_id: yaml-model-2
    name: YAML Model 2
    provider: openai
    dimensions: 1536
    max_input_tokens: 4096
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yaml_path = Path(tmpdir) / "models.yaml"
        yaml_path.write_text(yaml_content, encoding="utf-8")
        reg = EmbeddingRegistry(yaml_path=yaml_path)
        reg.load()
        models = reg.list_models()
        assert len(models) == 2
        m1 = reg.get_model("yaml-model-1")
        assert m1 is not None
        assert m1.dimensions == 64
        assert m1.provider == "stub"
        m2 = reg.get_model("yaml-model-2")
        assert m2 is not None
        assert m2.max_input_tokens == 4096

    print("PASS test_registry_yaml_load")


# --------------------------------------------------------------------- #
# Test 15: JSON overrides 加载 + 持久化
# --------------------------------------------------------------------- #

def test_registry_json_overrides():
    reset_for_tests()
    overrides_content = json.dumps({
        "models": [
            {
                "model_id": "override-model",
                "name": "Override",
                "provider": "stub",
                "dimensions": 128,
            }
        ]
    })
    with tempfile.TemporaryDirectory() as tmpdir:
        overrides_path = Path(tmpdir) / "overrides.json"
        overrides_path.write_text(overrides_content, encoding="utf-8")
        reg = EmbeddingRegistry(overrides_path=overrides_path)
        reg.load()
        m = reg.get_model("override-model")
        assert m is not None
        assert m.dimensions == 128

        # 新增模型 → 持久化到 overrides
        reg.register_model(_make_model("new-model"))
        saved = json.loads(overrides_path.read_text())
        ids = [x["model_id"] for x in saved["models"]]
        assert "new-model" in ids

    print("PASS test_registry_json_overrides")


# --------------------------------------------------------------------- #
# Test 16: OpenAIProvider 配置验证（无 key 时跳过 API 调用）
# --------------------------------------------------------------------- #

def test_openai_provider_config():
    # 仅验证构造和基本属性，不实际调用 API
    p = OpenAIProvider(api_key="sk-test", base_url="https://api.openai.com/v1")
    assert p._api_key == "sk-test"
    assert p._base_url == "https://api.openai.com/v1"

    # base_url trailing slash stripped
    p2 = OpenAIProvider(api_key="sk-test2", base_url="https://api.openai.com/v1/")
    assert p2._base_url == "https://api.openai.com/v1"

    print("PASS test_openai_provider_config")


# --------------------------------------------------------------------- #
# Test 17: EmbeddingProvider 基类不可直接使用
# --------------------------------------------------------------------- #

def test_provider_base_abstract():
    p = EmbeddingProvider()
    model = _make_model()

    async def run():
        try:
            await p.embed(["text"], model)
            assert False, "expected NotImplementedError"
        except NotImplementedError:
            pass

    _run_async(run())
    print("PASS test_provider_base_abstract")


# --------------------------------------------------------------------- #
# Test 18: 混合缓存命中 + 未命中
# --------------------------------------------------------------------- #

def test_service_partial_cache_hit():
    reset_embedding_service_for_tests()
    reg = _make_registry([_make_model("partial", dimensions=4)])
    svc = EmbeddingService(registry=reg)

    async def run():
        # 先 embed text1
        await svc.embed_one("partial", "text1")

        # 再 embed [text1, text2] — text1 命中，text2 未命中
        req = EmbeddingRequest(model_id="partial", texts=["text1", "text2"])
        resp = await svc.embed(req)
        assert resp.usage["cached_texts"] == 1
        assert resp.usage["computed_texts"] == 1
        assert len(resp.embeddings) == 2

    _run_async(run())
    print("PASS test_service_partial_cache_hit")


# --------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------- #

def main() -> int:
    tests = [
        test_embedding_model_fields,
        test_registry_register_get_list,
        test_registry_remove,
        test_registry_stats,
        test_stub_provider_deterministic,
        test_stub_provider_normalized,
        test_provider_factory_stub,
        test_provider_factory_openai_no_key,
        test_service_embed_cache_hit,
        test_service_embed_one,
        test_service_cache_stats_clear,
        test_service_unknown_model,
        test_global_singleton,
        test_registry_yaml_load,
        test_registry_json_overrides,
        test_openai_provider_config,
        test_provider_base_abstract,
        test_service_partial_cache_hit,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    total = len(tests)
    print(f"\n{total - failed}/{total} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
