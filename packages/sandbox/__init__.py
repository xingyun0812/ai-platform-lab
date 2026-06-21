"""packages.sandbox — 沙箱容器隔离 — Phase I #41

公开接口：
    SandboxConfig        沙箱执行配置
    SandboxResult        执行结果
    SandboxExecutor      执行器（线程安全）
    SandboxProfile       安全配置档案
    SECCOMP_PROFILES     预定义 seccomp 配置集合

    init_sandbox_executor(yaml_path, overrides_path)  初始化全局单例
    get_sandbox_executor()                            获取全局单例
    reset_sandbox_for_tests()                         重置（仅测试用）
"""

from __future__ import annotations

from packages.sandbox.executor import (
    SandboxConfig,
    SandboxExecutor,
    SandboxProfile,
    SandboxResult,
    get_sandbox_executor,
    init_sandbox_executor,
    reset_sandbox_for_tests,
)
from packages.sandbox.seccomp_profiles import SECCOMP_PROFILES

__all__ = [
    "SandboxConfig",
    "SandboxResult",
    "SandboxExecutor",
    "SandboxProfile",
    "SECCOMP_PROFILES",
    "init_sandbox_executor",
    "get_sandbox_executor",
    "reset_sandbox_for_tests",
]
