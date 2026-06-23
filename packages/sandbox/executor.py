"""沙箱执行器核心模块 — Phase I #41

提供三种运行时：
- process: 直接 asyncio.create_subprocess_exec（无真实隔离，仅用于开发/回退）
- docker:  docker run --rm --memory --cpus --security-opt seccomp=... --read-only
- gvisor:  docker run --rm --runtime=runsc ...（需宿主机安装 gVisor/runsc）

全局单例：
    init_sandbox_executor(yaml_path, overrides_path)
    get_sandbox_executor() -> SandboxExecutor | None
    reset_sandbox_for_tests()
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #


@dataclass
class SandboxProfile:
    """沙箱安全配置档案"""

    profile_id: str
    name: str
    seccomp_rules: dict = field(default_factory=dict)
    capabilities: list = field(default_factory=list)
    readonly_paths: list = field(default_factory=list)
    writable_paths: list = field(default_factory=list)
    network_enabled: bool = False
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "seccomp_rules": self.seccomp_rules,
            "capabilities": self.capabilities,
            "readonly_paths": self.readonly_paths,
            "writable_paths": self.writable_paths,
            "network_enabled": self.network_enabled,
            "created_at": self.created_at,
        }


@dataclass
class SandboxConfig:
    """单次沙箱执行的配置"""

    enabled: bool = True
    runtime: str = "process"        # "process" | "docker" | "gvisor"
    image: str = "python:3.11-slim"
    memory_limit_mb: int = 256
    cpu_limit: float = 0.5
    timeout_seconds: float = 30.0
    profile_id: str = "default"
    env: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "runtime": self.runtime,
            "image": self.image,
            "memory_limit_mb": self.memory_limit_mb,
            "cpu_limit": self.cpu_limit,
            "timeout_seconds": self.timeout_seconds,
            "profile_id": self.profile_id,
            "env": self.env,
        }


@dataclass
class SandboxResult:
    """沙箱执行结果"""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
        }


# --------------------------------------------------------------------------- #
# SandboxExecutor
# --------------------------------------------------------------------------- #


class SandboxExecutor:
    """线程安全的沙箱执行器，管理配置档案并执行隔离命令。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._profiles: dict[str, SandboxProfile] = {}

    # ------------------------------------------------------------------ #
    # 配置档案管理
    # ------------------------------------------------------------------ #

    def register_profile(self, profile: SandboxProfile) -> SandboxProfile:
        with self._lock:
            self._profiles[profile.profile_id] = profile
            logger.debug("sandbox: registered profile %s", profile.profile_id)
            return profile

    def get_profile(self, profile_id: str) -> SandboxProfile | None:
        with self._lock:
            return self._profiles.get(profile_id)

    def remove_profile(self, profile_id: str) -> bool:
        with self._lock:
            if profile_id in self._profiles:
                del self._profiles[profile_id]
                return True
            return False

    def list_profiles(self) -> list[SandboxProfile]:
        with self._lock:
            return list(self._profiles.values())

    # ------------------------------------------------------------------ #
    # YAML / JSON 加载
    # ------------------------------------------------------------------ #

    def _load_yaml(self, yaml_path: Path | None) -> None:
        if not yaml_path or not Path(yaml_path).exists():
            return
        try:
            import yaml  # type: ignore[import]

            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            for pid, cfg in data.items():
                profile = SandboxProfile(
                    profile_id=pid,
                    name=cfg.get("name", pid),
                    seccomp_rules=cfg.get("seccomp_rules", {}),
                    capabilities=cfg.get("capabilities", []),
                    readonly_paths=cfg.get("readonly_paths", []),
                    writable_paths=cfg.get("writable_paths", []),
                    network_enabled=cfg.get("network_enabled", False),
                )
                self.register_profile(profile)
            logger.info("sandbox: loaded %d profiles from YAML", len(data))
        except Exception as exc:  # noqa: BLE001
            logger.warning("sandbox: failed to load YAML profiles: %s", exc)

    def _load_json_overrides(self, overrides_path: Path | None) -> None:
        if not overrides_path or not Path(overrides_path).exists():
            return
        try:
            with open(overrides_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                items = data
            else:
                items = data.get("profiles", [])
            for item in items:
                profile = SandboxProfile(
                    profile_id=item["profile_id"],
                    name=item.get("name", item["profile_id"]),
                    seccomp_rules=item.get("seccomp_rules", {}),
                    capabilities=item.get("capabilities", []),
                    readonly_paths=item.get("readonly_paths", []),
                    writable_paths=item.get("writable_paths", []),
                    network_enabled=item.get("network_enabled", False),
                    created_at=item.get("created_at", time.time()),
                )
                self.register_profile(profile)
            logger.info("sandbox: applied JSON overrides from %s", overrides_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sandbox: failed to load JSON overrides: %s", exc)

    def persist_profiles(self, overrides_path: Path) -> None:
        """将当前所有档案持久化到 JSON。"""
        Path(overrides_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            profiles_list = [p.to_dict() for p in self._profiles.values()]
        with open(overrides_path, "w", encoding="utf-8") as f:
            json.dump({"profiles": profiles_list}, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #

    async def execute(self, command: list[str], config: SandboxConfig) -> SandboxResult:
        """根据 config.runtime 选择执行策略。"""
        runtime = config.runtime
        if runtime == "process":
            return await self._execute_process(command, config)
        elif runtime == "docker":
            return await self._execute_docker(command, config, gvisor=False)
        elif runtime == "gvisor":
            return await self._execute_docker(command, config, gvisor=True)
        else:
            logger.warning("sandbox: unknown runtime %r, falling back to process", runtime)
            return await self._execute_process(command, config)

    async def _execute_process(
        self, command: list[str], config: SandboxConfig
    ) -> SandboxResult:
        """直接进程执行（无真实隔离，仅用于开发/回退）。"""
        import os

        env = {**os.environ}
        if config.env:
            env.update(config.env)

        t0 = time.time()
        timed_out = False
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=config.timeout_seconds
                )
            except TimeoutError:
                timed_out = True
                try:
                    proc.kill()
                except Exception:
                    pass
                stdout_bytes, stderr_bytes = b"", b"timeout"
                exit_code = -1
            else:
                exit_code = proc.returncode if proc.returncode is not None else -1
        except FileNotFoundError:
            return SandboxResult(
                exit_code=127,
                stdout="",
                stderr=f"command not found: {command[0]}",
                duration_ms=0.0,
                timed_out=False,
            )
        except Exception as exc:  # noqa: BLE001
            return SandboxResult(
                exit_code=1,
                stdout="",
                stderr=str(exc),
                duration_ms=(time.time() - t0) * 1000,
                timed_out=False,
            )

        duration_ms = (time.time() - t0) * 1000
        if not timed_out:
            return SandboxResult(
                exit_code=exit_code,
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr_bytes.decode("utf-8", errors="replace"),
                duration_ms=duration_ms,
                timed_out=False,
            )
        return SandboxResult(
            exit_code=-1,
            stdout="",
            stderr="timeout",
            duration_ms=duration_ms,
            timed_out=True,
        )

    async def _execute_docker(
        self, command: list[str], config: SandboxConfig, gvisor: bool = False
    ) -> SandboxResult:
        """通过 Docker（或 gVisor）执行命令。"""
        import json as _json
        import os
        import tempfile

        docker_cmd = ["docker", "run", "--rm"]

        # runtime
        if gvisor:
            docker_cmd += ["--runtime=runsc"]

        # 资源限制
        docker_cmd += [
            f"--memory={config.memory_limit_mb}m",
            f"--cpus={config.cpu_limit}",
        ]

        # seccomp
        profile = self.get_profile(config.profile_id)
        seccomp_file = None
        if profile and profile.seccomp_rules:
            try:
                tf = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False, encoding="utf-8"
                )
                _json.dump(profile.seccomp_rules, tf)
                tf.flush()
                tf.close()
                seccomp_file = tf.name
                docker_cmd += [f"--security-opt=seccomp={seccomp_file}"]
            except Exception as exc:
                logger.warning("sandbox: failed to write seccomp file: %s", exc)

        # 只读根文件系统
        docker_cmd += ["--read-only"]

        # capabilities
        if profile and profile.capabilities:
            for cap in profile.capabilities:
                docker_cmd += [f"--cap-add={cap}"]
        else:
            docker_cmd += ["--cap-drop=ALL"]

        # 网络
        if profile and profile.network_enabled:
            pass  # 默认 bridge 网络
        else:
            docker_cmd += ["--network=none"]

        # 可写挂载
        if profile:
            for wp in profile.writable_paths:
                docker_cmd += [f"--tmpfs={wp}"]

        # 环境变量
        for k, v in config.env.items():
            docker_cmd += ["-e", f"{k}={v}"]

        docker_cmd += [config.image] + command

        t0 = time.time()
        timed_out = False
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=dict(os.environ),
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=config.timeout_seconds
                )
                exit_code = proc.returncode if proc.returncode is not None else -1
            except TimeoutError:
                timed_out = True
                try:
                    proc.kill()
                except Exception:
                    pass
                stdout_bytes, stderr_bytes = b"", b"timeout"
                exit_code = -1
        except FileNotFoundError:
            return SandboxResult(
                exit_code=127,
                stdout="",
                stderr="docker not found; install Docker or use runtime=process",
                duration_ms=0.0,
                timed_out=False,
            )
        except Exception as exc:
            return SandboxResult(
                exit_code=1,
                stdout="",
                stderr=str(exc),
                duration_ms=(time.time() - t0) * 1000,
                timed_out=False,
            )
        finally:
            if seccomp_file:
                try:
                    os.unlink(seccomp_file)
                except Exception:
                    pass

        duration_ms = (time.time() - t0) * 1000
        if timed_out:
            return SandboxResult(
                exit_code=-1, stdout="", stderr="timeout", duration_ms=duration_ms, timed_out=True
            )
        return SandboxResult(
            exit_code=exit_code,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration_ms=duration_ms,
            timed_out=False,
        )


# --------------------------------------------------------------------------- #
# 全局单例
# --------------------------------------------------------------------------- #

_executor: SandboxExecutor | None = None
_executor_lock = threading.Lock()


def init_sandbox_executor(
    yaml_path: Path | None = None,
    overrides_path: Path | None = None,
) -> SandboxExecutor:
    """初始化全局沙箱执行器（幂等）。"""
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = SandboxExecutor()
        _executor._load_yaml(yaml_path)
        _executor._load_json_overrides(overrides_path)
        return _executor


def get_sandbox_executor() -> SandboxExecutor | None:
    """获取全局沙箱执行器（未初始化则返回 None）。"""
    return _executor


def reset_sandbox_for_tests() -> None:
    """重置全局单例（仅用于测试）。"""
    global _executor
    with _executor_lock:
        _executor = None
