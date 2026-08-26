"""reflection_policy 分级判定 + 配置解析单测（零外部依赖）。"""

from __future__ import annotations

import pytest

from packages.agent.reflection_policy import (
    DEFAULT_DEPTH,
    ReflectionPolicy,
)


def test_default_depth_is_light() -> None:
    policy = ReflectionPolicy()
    assert policy.default_depth == DEFAULT_DEPTH == "light"
    assert policy.resolve(None) == "light"
    assert policy.resolve("") == "light"
    assert policy.resolve("default") == "light"


def test_resolve_explicit_depth() -> None:
    policy = ReflectionPolicy()
    assert policy.resolve("off") == "off"
    assert policy.resolve("full") == "full"
    assert policy.resolve("light") == "light"
    assert policy.resolve("legacy") == "legacy"


def test_resolve_invalid_depth_raises() -> None:
    policy = ReflectionPolicy()
    with pytest.raises(ValueError):
        policy.resolve("super")


def test_invalid_default_depth_raises() -> None:
    with pytest.raises(ValueError):
        ReflectionPolicy(default_depth="nope")


def test_invalid_confidence_threshold_raises() -> None:
    with pytest.raises(ValueError):
        ReflectionPolicy(confidence_threshold=1.5)
    with pytest.raises(ValueError):
        ReflectionPolicy(confidence_threshold=-0.1)


def test_off_disables_llm() -> None:
    policy = ReflectionPolicy(default_depth="off")
    assert policy.is_off("off") is True
    assert policy.is_enabled("off") is False
    # 不同深度独立
    assert policy.is_enabled("light") is True
    assert policy.is_enabled("full") is True


def test_full_is_async_light_is_sync() -> None:
    policy = ReflectionPolicy()
    assert policy.is_async("full") is True
    assert policy.is_async("light") is False
    assert policy.is_async("off") is False


def test_profile_defaults_match_depth() -> None:
    policy = ReflectionPolicy()
    assert policy.profile("light").max_iterations == 1
    assert policy.profile("light").max_total_llm_calls == 1
    assert policy.profile("off").max_total_llm_calls == 0
    assert policy.profile("full").max_iterations >= 3
    assert policy.profile("legacy").max_total_latency_s > 0


def test_from_dict_custom_depth_and_profile() -> None:
    cfg = {
        "default_depth": "full",
        "confidence_threshold": 0.7,
        "dedup_enabled": False,
        "light": {"model": "cheap-model", "max_iterations": 1, "max_total_latency_s": 5.0},
        "full": {"model": "big-model", "max_iterations": 6, "async_offload": True},
    }
    policy = ReflectionPolicy.from_dict(cfg)
    assert policy.default_depth == "full"
    assert policy.confidence_threshold == 0.7
    assert policy.dedup_enabled is False
    assert policy.profile("light").model == "cheap-model"
    assert policy.profile("light").max_total_latency_s == 5.0
    assert policy.profile("full").max_iterations == 6
    assert policy.profile("full").async_offload is True
    # 未配置的深度回退内置默认
    assert policy.profile("off").max_total_llm_calls == 0


def test_from_dict_missing_keys_falls_back() -> None:
    policy = ReflectionPolicy.from_dict(None)
    assert policy.default_depth == "light"
    assert policy.confidence_threshold == 0.85
    assert policy.dedup_enabled is True


def test_from_dict_explicit_default_depth_overrides() -> None:
    policy = ReflectionPolicy.from_dict({"default_depth": "light"}, default_depth="off")
    assert policy.default_depth == "off"


def test_load_zero_dependency() -> None:
    policy = ReflectionPolicy.load(reflection_override={"default_depth": "off"})
    assert policy.default_depth == "off"
    default = ReflectionPolicy.load()
    assert default.default_depth == "light"


def test_to_dict_roundtrip() -> None:
    policy = ReflectionPolicy.from_dict({"default_depth": "full", "light": {"max_iterations": 2}})
    as_dict = policy.to_dict()
    assert as_dict["default_depth"] == "full"
    assert as_dict["per_depth"]["light"]["max_iterations"] == 2
    reloaded = ReflectionPolicy.from_dict(as_dict)
    assert reloaded.default_depth == policy.default_depth
    assert reloaded.profile("light").max_iterations == 2
