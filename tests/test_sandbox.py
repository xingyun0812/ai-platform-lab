#!/usr/bin/env python3
"""沙箱容器隔离单元测试 — Phase I #41

运行：
    python3 tests/test_sandbox.py

覆盖 ≥12 个测试用例，包括：
- SandboxProfile 数据模型
- SandboxConfig 默认值
- SandboxResult 数据模型
- SandboxExecutor profile 管理（注册/列出/获取/删除）
- seccomp 预定义配置结构校验
- process runtime 实际执行（echo hello）
- 超时场景
- 配置档案 YAML 加载（mock 路径）
- JSON 持久化 & 覆盖加载
- singleton 模式（init/get/reset）
- SandboxConfig.to_dict
- 工具包装器（sandbox disabled 路径）
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# --------------------------------------------------------------------------- #
# 模块加载工具
# --------------------------------------------------------------------------- #


def _load_module(module_name: str, file_path: Path):
    """加载模块并注册到 sys.modules（Python 3.9 dataclass 需要）。"""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_seccomp_module():
    return _load_module(
        "sandbox_seccomp_test",
        REPO_ROOT / "packages" / "sandbox" / "seccomp_profiles.py",
    )


def _load_executor_module():
    return _load_module(
        "sandbox_executor_test",
        REPO_ROOT / "packages" / "sandbox" / "executor.py",
    )


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --------------------------------------------------------------------------- #
# 测试用例
# --------------------------------------------------------------------------- #


def test_seccomp_profiles_keys():
    """SECCOMP_PROFILES 包含四个预定义配置"""
    mod = _load_seccomp_module()
    profiles = mod.SECCOMP_PROFILES
    assert "strict" in profiles
    assert "default" in profiles
    assert "networking" in profiles
    assert "readonly" in profiles
    print("PASS test_seccomp_profiles_keys")


def test_seccomp_profile_format():
    """每个 seccomp 配置都有 defaultAction 和 syscalls 字段"""
    mod = _load_seccomp_module()
    for name, profile in mod.SECCOMP_PROFILES.items():
        assert "defaultAction" in profile, f"{name} 缺少 defaultAction"
        assert "syscalls" in profile, f"{name} 缺少 syscalls"
        assert isinstance(profile["syscalls"], list), f"{name}.syscalls 应为 list"
        for entry in profile["syscalls"]:
            assert "names" in entry, f"{name}.syscalls 条目缺少 names"
            assert "action" in entry, f"{name}.syscalls 条目缺少 action"
    print("PASS test_seccomp_profile_format")


def test_seccomp_strict_allows_minimal_syscalls():
    """strict 配置应允许极少数系统调用"""
    mod = _load_seccomp_module()
    strict = mod.SECCOMP_PROFILES["strict"]
    assert strict["defaultAction"] == "SCMP_ACT_ERRNO"
    allowed_names = []
    for entry in strict["syscalls"]:
        if entry["action"] == "SCMP_ACT_ALLOW":
            allowed_names.extend(entry["names"])
    assert "read" in allowed_names
    assert "write" in allowed_names
    assert "exit" in allowed_names
    print("PASS test_seccomp_strict_allows_minimal_syscalls")


def test_sandbox_profile_dataclass():
    """SandboxProfile 数据模型字段默认值"""
    mod = _load_executor_module()
    profile = mod.SandboxProfile(profile_id="test_p", name="Test Profile")
    assert profile.profile_id == "test_p"
    assert profile.name == "Test Profile"
    assert profile.network_enabled is False
    assert isinstance(profile.seccomp_rules, dict)
    assert isinstance(profile.capabilities, list)
    d = profile.to_dict()
    assert d["profile_id"] == "test_p"
    assert "created_at" in d
    print("PASS test_sandbox_profile_dataclass")


def test_sandbox_config_defaults():
    """SandboxConfig 默认值"""
    mod = _load_executor_module()
    config = mod.SandboxConfig()
    assert config.enabled is True
    assert config.runtime == "process"
    assert config.image == "python:3.11-slim"
    assert config.memory_limit_mb == 256
    assert config.cpu_limit == 0.5
    assert config.timeout_seconds == 30.0
    assert config.profile_id == "default"
    assert isinstance(config.env, dict)
    d = config.to_dict()
    assert d["runtime"] == "process"
    print("PASS test_sandbox_config_defaults")


def test_sandbox_result_dataclass():
    """SandboxResult 数据模型"""
    mod = _load_executor_module()
    result = mod.SandboxResult(
        exit_code=0,
        stdout="hello",
        stderr="",
        duration_ms=12.5,
        timed_out=False,
    )
    assert result.exit_code == 0
    assert result.stdout == "hello"
    assert result.timed_out is False
    d = result.to_dict()
    assert d["exit_code"] == 0
    assert d["stdout"] == "hello"
    assert d["duration_ms"] == 12.5
    print("PASS test_sandbox_result_dataclass")


def test_executor_profile_register_and_get():
    """executor 注册/获取档案"""
    mod = _load_executor_module()
    exec_ = mod.SandboxExecutor()
    profile = mod.SandboxProfile(profile_id="p1", name="Profile 1")
    exec_.register_profile(profile)
    got = exec_.get_profile("p1")
    assert got is not None
    assert got.name == "Profile 1"
    missing = exec_.get_profile("nonexistent")
    assert missing is None
    print("PASS test_executor_profile_register_and_get")


def test_executor_profile_list_and_remove():
    """executor 列出和删除档案"""
    mod = _load_executor_module()
    exec_ = mod.SandboxExecutor()
    exec_.register_profile(mod.SandboxProfile(profile_id="a", name="A"))
    exec_.register_profile(mod.SandboxProfile(profile_id="b", name="B"))
    profiles = exec_.list_profiles()
    assert len(profiles) == 2
    ids = {p.profile_id for p in profiles}
    assert "a" in ids and "b" in ids

    ok = exec_.remove_profile("a")
    assert ok is True
    assert exec_.get_profile("a") is None
    assert len(exec_.list_profiles()) == 1

    not_ok = exec_.remove_profile("nonexistent")
    assert not_ok is False
    print("PASS test_executor_profile_list_and_remove")


def test_executor_process_runtime_echo():
    """process runtime 执行 echo hello"""
    mod = _load_executor_module()
    exec_ = mod.SandboxExecutor()
    config = mod.SandboxConfig(runtime="process", timeout_seconds=10.0)

    async def run():
        return await exec_.execute(["echo", "hello"], config)

    result = _run_async(run())
    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert result.timed_out is False
    assert result.duration_ms >= 0
    print("PASS test_executor_process_runtime_echo")


def test_executor_process_runtime_timeout():
    """process runtime 超时场景"""
    mod = _load_executor_module()
    exec_ = mod.SandboxExecutor()
    config = mod.SandboxConfig(runtime="process", timeout_seconds=0.1)

    async def run():
        # sleep 5 秒应触发超时
        return await exec_.execute(["sleep", "5"], config)

    result = _run_async(run())
    assert result.timed_out is True
    assert result.exit_code == -1
    print("PASS test_executor_process_runtime_timeout")


def test_executor_json_persist_and_reload():
    """JSON 持久化与覆盖加载"""
    mod = _load_executor_module()
    exec1 = mod.SandboxExecutor()
    exec1.register_profile(
        mod.SandboxProfile(
            profile_id="persist_test",
            name="Persist Test",
            network_enabled=True,
        )
    )

    with tempfile.NamedTemporaryFile(
        suffix=".json", mode="w", delete=False, encoding="utf-8"
    ) as f:
        tmp_path = f.name
    exec1.persist_profiles(Path(tmp_path))

    # 新执行器从 JSON 加载
    exec2 = mod.SandboxExecutor()
    exec2._load_json_overrides(Path(tmp_path))
    loaded = exec2.get_profile("persist_test")
    assert loaded is not None
    assert loaded.name == "Persist Test"
    assert loaded.network_enabled is True

    import os
    os.unlink(tmp_path)
    print("PASS test_executor_json_persist_and_reload")


def test_singleton_init_get_reset():
    """全局单例 init/get/reset"""
    mod = _load_executor_module()
    # 确保初始为 None
    mod.reset_sandbox_for_tests()
    assert mod.get_sandbox_executor() is None

    # 初始化
    executor = mod.init_sandbox_executor()
    assert executor is not None
    assert mod.get_sandbox_executor() is executor

    # 幂等 init（第二次调用返回同一实例）
    executor2 = mod.init_sandbox_executor()
    assert executor2 is executor

    # 重置
    mod.reset_sandbox_for_tests()
    assert mod.get_sandbox_executor() is None
    print("PASS test_singleton_init_get_reset")


def test_tool_wrapper_sandbox_disabled():
    """sandbox disabled 时工具包装器直接返回"""
    # 加载 tool_wrapper 模块（懒导入，避免循环）
    wrapper_mod = _load_module(
        "sandbox_tool_wrapper_test",
        REPO_ROOT / "packages" / "sandbox" / "tool_wrapper.py",
    )
    exec_mod = _load_executor_module()
    config = exec_mod.SandboxConfig(enabled=False)

    async def run():
        return await wrapper_mod.execute_tool_in_sandbox(
            tool_name="test_tool",
            arguments={"key": "value"},
            config=config,
        )

    result_str = _run_async(run())
    result = json.loads(result_str)
    assert result["result"] == "sandbox_disabled"
    assert result["tool"] == "test_tool"
    print("PASS test_tool_wrapper_sandbox_disabled")


def test_executor_unknown_command():
    """执行不存在的命令应返回 exit_code=127"""
    mod = _load_executor_module()
    exec_ = mod.SandboxExecutor()
    config = mod.SandboxConfig(runtime="process", timeout_seconds=5.0)

    async def run():
        return await exec_.execute(["nonexistent_cmd_xyz_12345"], config)

    result = _run_async(run())
    assert result.exit_code == 127
    assert "not found" in result.stderr
    print("PASS test_executor_unknown_command")


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #


def main() -> int:
    tests = [
        test_seccomp_profiles_keys,
        test_seccomp_profile_format,
        test_seccomp_strict_allows_minimal_syscalls,
        test_sandbox_profile_dataclass,
        test_sandbox_config_defaults,
        test_sandbox_result_dataclass,
        test_executor_profile_register_and_get,
        test_executor_profile_list_and_remove,
        test_executor_process_runtime_echo,
        test_executor_process_runtime_timeout,
        test_executor_json_persist_and_reload,
        test_singleton_init_get_reset,
        test_tool_wrapper_sandbox_disabled,
        test_executor_unknown_command,
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
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
