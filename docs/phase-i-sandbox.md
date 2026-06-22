# Phase I — 沙箱容器隔离（#41）

> **目标**：为工具执行添加 Docker seccomp 安全配置 + 可选 gVisor 支持，将危险工具调用封装在隔离的沙箱执行层中。

对标「AI Agent 安全架构」中的「工具执行隔离」能力，防止恶意代码（如工具注入攻击）对宿主机造成损害。

---

构建思路、使用链路与逐文件代码说明见 [phase-i-build-and-code-guide.md](./phase-i-build-and-code-guide.md)。

## 1. 设计要点

### 1.1 安全威胁模型

| 威胁 | 描述 | 沙箱对策 |
|------|------|---------|
| 工具注入攻击 | LLM 生成的工具参数中包含恶意命令 | 进程/容器隔离 |
| 文件系统越权 | 工具试图读写敏感文件 | `--read-only` + seccomp 限制写系统调用 |
| 网络横向渗透 | 工具建立未授权网络连接 | `--network=none` 或 `bind` 禁用 |
| 特权提升 | 工具尝试 `mount`/`chroot`/`ptrace` | seccomp `SCMP_ACT_ERRNO` 拦截 |
| 资源耗尽 | 工具消耗大量 CPU/内存 | `--memory` + `--cpus` + `timeout` |

### 1.2 三种运行时模式

| 运行时 | 隔离级别 | 适用场景 | 备注 |
|--------|---------|---------|------|
| `process` | 无（直接子进程） | 开发/测试回退 | 无容器隔离，仅作 fallback |
| `docker` | 容器 + seccomp + capabilities | 生产首选 | 需宿主机安装 Docker |
| `gvisor` | 容器 + seccomp + 内核拦截 | 高安全场景 | 需宿主机安装 gVisor (runsc) |

#### process 模式
```python
proc = await asyncio.create_subprocess_exec(*command, env=restricted_env, ...)
stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=config.timeout_seconds)
```
无真实内核隔离，仅用于开发调试或不需隔离的场景。

#### docker 模式
```bash
docker run --rm \
  --memory=256m --cpus=0.5 \
  --security-opt seccomp=<profile.json> \
  --read-only \
  --cap-drop=ALL \
  --network=none \
  python:3.11-slim <command>
```

#### gvisor 模式
```bash
docker run --rm --runtime=runsc \
  --memory=256m --cpus=0.5 \
  --security-opt seccomp=<profile.json> \
  --read-only \
  --cap-drop=ALL \
  --network=none \
  python:3.11-slim <command>
```
gVisor 在用户态重新实现了 Linux 系统调用，即使 seccomp 被绕过也有内核拦截层。

### 1.3 seccomp 配置档案（SECCOMP_PROFILES）

| profile_id | defaultAction | 说明 |
|-----------|--------------|------|
| `strict` | `SCMP_ACT_ERRNO` | 仅允许 read/write/exit/brk/mmap 等极少系统调用 |
| `default` | `SCMP_ACT_ALLOW` | 拒绝 mount/reboot/chroot/ptrace 等危险调用 |
| `networking` | `SCMP_ACT_ALLOW` | 拒绝 bind/listen，允许 socket/connect |
| `readonly` | `SCMP_ACT_ALLOW` | 拒绝所有写系统调用（write/open/creat 等）|

seccomp JSON 格式：
```json
{
  "defaultAction": "SCMP_ACT_ERRNO",
  "architectures": ["SCMP_ARCH_X86_64"],
  "syscalls": [
    {
      "names": ["read", "write", "exit"],
      "action": "SCMP_ACT_ALLOW"
    }
  ]
}
```

### 1.4 SandboxProfile 自定义档案

用户可通过 REST API 或 YAML/JSON 文件定义自定义档案：

```yaml
# config/sandbox_profiles.yaml
code_exec:
  name: "代码执行档案"
  seccomp_rules:
    defaultAction: "SCMP_ACT_ALLOW"
    syscalls:
      - names: [mount, reboot, chroot, ptrace]
        action: SCMP_ACT_ERRNO
  capabilities: []
  readonly_paths: ["/usr", "/lib"]
  writable_paths: ["/tmp"]
  network_enabled: false
```

### 1.5 工具包装器集成点

```python
# Agent Registry 中标注需要沙箱的工具
@tool(requires_sandbox=True)
async def execute_code(code: str) -> str:
    ...

# 路由到沙箱
result = await execute_tool_in_sandbox(
    tool_name="execute_code",
    arguments={"code": code},
    config=SandboxConfig(runtime="docker", profile_id="code_exec"),
)
```

---

## 2. 数据模型

### SandboxProfile
```python
@dataclass
class SandboxProfile:
    profile_id: str                    # 唯一标识
    name: str                          # 显示名
    seccomp_rules: dict                # Docker seccomp JSON 格式
    capabilities: list[str]            # 额外 Linux capabilities
    readonly_paths: list[str]          # 只读挂载路径
    writable_paths: list[str]          # 可写挂载路径（tmpfs）
    network_enabled: bool              # 是否启用网络（默认 False）
    created_at: float                  # 创建时间戳
```

### SandboxConfig
```python
@dataclass
class SandboxConfig:
    enabled: bool          = True        # 是否启用沙箱
    runtime: str           = "process"   # "process" | "docker" | "gvisor"
    image: str             = "python:3.11-slim"  # 容器镜像
    memory_limit_mb: int   = 256         # 内存限制（MB）
    cpu_limit: float       = 0.5         # CPU 核数限制
    timeout_seconds: float = 30.0        # 执行超时（秒）
    profile_id: str        = "default"   # 安全档案 ID
    env: dict[str, str]    = {}          # 额外环境变量
```

### SandboxResult
```python
@dataclass
class SandboxResult:
    exit_code: int        # 进程退出码（超时时为 -1）
    stdout: str           # 标准输出
    stderr: str           # 标准错误
    duration_ms: float    # 执行耗时（毫秒）
    timed_out: bool       # 是否超时
```

---

## 3. REST API

| Method | Path | 描述 | 权限 |
|--------|------|------|------|
| GET | `/internal/sandbox/profiles` | 列出所有配置档案 | 所有租户 |
| GET | `/internal/sandbox/profiles/{profile_id}` | 获取档案详情 | 所有租户 |
| POST | `/internal/sandbox/profiles` | 注册新档案 | platform_admin |
| DELETE | `/internal/sandbox/profiles/{profile_id}` | 删除档案 | platform_admin |
| POST | `/internal/sandbox/execute` | 执行沙箱命令 | platform_admin |
| GET | `/internal/sandbox/status` | 检查 docker/gvisor 可用性 | 所有租户 |

### POST /internal/sandbox/execute 请求体
```json
{
  "command": ["python3", "-c", "print('hello')"],
  "config": {
    "enabled": true,
    "runtime": "docker",
    "image": "python:3.11-slim",
    "memory_limit_mb": 128,
    "cpu_limit": 0.25,
    "timeout_seconds": 10.0,
    "profile_id": "default",
    "env": {}
  }
}
```

### SandboxResult 响应
```json
{
  "exit_code": 0,
  "stdout": "hello\n",
  "stderr": "",
  "duration_ms": 234.5,
  "timed_out": false
}
```

---

## 4. 配置项（Settings）

> 以下字段需在 `apps/gateway/settings.py` 的 `Settings` 类中添加（由 parent agent 集成）：

| 字段名 | 环境变量 | 默认值 | 描述 |
|--------|---------|--------|------|
| `sandbox_enabled` | `SANDBOX_ENABLED` | `False` | 启用工具沙箱隔离 |
| `sandbox_default_runtime` | `SANDBOX_DEFAULT_RUNTIME` | `"process"` | 默认运行时: process/docker/gvisor |
| `sandbox_default_image` | `SANDBOX_DEFAULT_IMAGE` | `"python:3.11-slim"` | 默认容器镜像 |
| `sandbox_profiles_config_path` | `SANDBOX_PROFILES_CONFIG_PATH` | `REPO_ROOT / "config" / "sandbox_profiles.yaml"` | 档案配置文件路径 |
| `sandbox_profiles_overrides_path` | `SANDBOX_PROFILES_OVERRIDES_PATH` | `REPO_ROOT / "data" / "sandbox_profiles_overrides.json"` | 档案覆盖文件路径 |
| `sandbox_default_memory_mb` | `SANDBOX_DEFAULT_MEMORY_MB` | `256` | 默认内存限制（MB）|
| `sandbox_default_cpu_limit` | `SANDBOX_DEFAULT_CPU_LIMIT` | `0.5` | 默认 CPU 核数限制 |
| `sandbox_default_timeout_seconds` | `SANDBOX_DEFAULT_TIMEOUT_SECONDS` | `30.0` | 默认超时（秒）|

### settings.py 代码片段

```python
sandbox_enabled: bool = Field(default=False, validation_alias="SANDBOX_ENABLED", description="启用工具沙箱隔离")
sandbox_default_runtime: str = Field(default="process", validation_alias="SANDBOX_DEFAULT_RUNTIME", description="默认运行时: process/docker/gvisor")
sandbox_default_image: str = Field(default="python:3.11-slim", validation_alias="SANDBOX_DEFAULT_IMAGE", description="默认容器镜像")
sandbox_profiles_config_path: Path = Field(default=REPO_ROOT / "config" / "sandbox_profiles.yaml", validation_alias="SANDBOX_PROFILES_CONFIG_PATH")
sandbox_profiles_overrides_path: Path = Field(default=REPO_ROOT / "data" / "sandbox_profiles_overrides.json", validation_alias="SANDBOX_PROFILES_OVERRIDES_PATH")
sandbox_default_memory_mb: int = Field(default=256, validation_alias="SANDBOX_DEFAULT_MEMORY_MB")
sandbox_default_cpu_limit: float = Field(default=0.5, validation_alias="SANDBOX_DEFAULT_CPU_LIMIT")
sandbox_default_timeout_seconds: float = Field(default=30.0, validation_alias="SANDBOX_DEFAULT_TIMEOUT_SECONDS")
```

---

## 5. main.py 集成

> 以下代码需由 parent agent 添加到 `apps/gateway/main.py`：

```python
from apps.gateway.sandbox_routes import router as sandbox_router

# 在 lifespan 或启动逻辑中：
if settings.sandbox_enabled:
    from packages.sandbox import init_sandbox_executor
    init_sandbox_executor(
        yaml_path=settings.sandbox_profiles_config_path,
        overrides_path=settings.sandbox_profiles_overrides_path,
    )

app.include_router(sandbox_router)
```

---

## 6. .env.example 新增条目

```bash
# ── Sandbox 沙箱隔离 ───────────────────────────────────────────────────
SANDBOX_ENABLED=false
SANDBOX_DEFAULT_RUNTIME=process      # process | docker | gvisor
SANDBOX_DEFAULT_IMAGE=python:3.11-slim
SANDBOX_PROFILES_CONFIG_PATH=config/sandbox_profiles.yaml
SANDBOX_PROFILES_OVERRIDES_PATH=data/sandbox_profiles_overrides.json
SANDBOX_DEFAULT_MEMORY_MB=256
SANDBOX_DEFAULT_CPU_LIMIT=0.5
SANDBOX_DEFAULT_TIMEOUT_SECONDS=30.0
```

---

## 7. README 新增章节

> 以下内容建议添加到 README.md 的「功能模块」章节：

```markdown
### Phase I — 沙箱容器隔离（#41）

通过 Docker seccomp + 可选 gVisor 为工具执行提供隔离层：
- **三种运行时**：`process`（开发回退）/ `docker`（生产首选）/ `gvisor`（高安全）
- **预定义 seccomp 档案**：`strict` / `default` / `networking` / `readonly`
- **可配置限制**：内存、CPU、超时、只读文件系统、网络禁用
- **REST API**：`/internal/sandbox/profiles`、`/internal/sandbox/execute`、`/internal/sandbox/status`
- 启用：`SANDBOX_ENABLED=true`，推荐搭配 `SANDBOX_DEFAULT_RUNTIME=docker`
```

---

## 8. roadmap.md 更新

> 以下条目需添加到 `docs/roadmap.md`：

```markdown
| #41 | Phase I | 沙箱容器隔离 | Docker seccomp + gVisor | ✅ Done |
```

---

## 9. 测试

```bash
python3 tests/test_sandbox.py
```

| 测试名 | 覆盖点 |
|--------|--------|
| `test_seccomp_profiles_keys` | 预定义配置 key 存在 |
| `test_seccomp_profile_format` | seccomp JSON 格式结构 |
| `test_seccomp_strict_allows_minimal_syscalls` | strict 配置内容 |
| `test_sandbox_profile_dataclass` | SandboxProfile 字段默认值 |
| `test_sandbox_config_defaults` | SandboxConfig 默认值 |
| `test_sandbox_result_dataclass` | SandboxResult to_dict |
| `test_executor_profile_register_and_get` | 档案注册/获取 |
| `test_executor_profile_list_and_remove` | 档案列出/删除 |
| `test_executor_process_runtime_echo` | process runtime 实际执行 |
| `test_executor_process_runtime_timeout` | 超时场景 |
| `test_executor_json_persist_and_reload` | JSON 持久化与重载 |
| `test_singleton_init_get_reset` | 全局单例模式 |
| `test_tool_wrapper_sandbox_disabled` | 工具包装器 disabled 路径 |
| `test_executor_unknown_command` | 不存在的命令处理 |

---

## 10. 代码导航

```
packages/sandbox/
├── __init__.py              # 包公开接口（exports）
├── seccomp_profiles.py      # 预定义 seccomp 配置集合（SECCOMP_PROFILES）
├── executor.py              # 核心：SandboxProfile/Config/Result + SandboxExecutor
└── tool_wrapper.py          # 工具调用包装器（Agent 集成点）

apps/gateway/
└── sandbox_routes.py        # REST API（/internal/sandbox 前缀）

tests/
└── test_sandbox.py          # 14 个测试用例

docs/
└── phase-i-sandbox.md       # 本文档
```

---

## 11. 已知限制

1. **gVisor 需宿主机支持**：`runsc` 运行时须在宿主 Linux 内核上预安装 gVisor，macOS 和 Windows 不支持。
2. **seccomp 仅在 Linux 生效**：Docker 在 macOS 上通过 HyperKit/Rosetta 运行，seccomp 配置对宿主机无效；需在 Linux 生产环境才能真正启用。
3. **无 cgroup v2 强制执行**：当前实现通过 `--memory`/`--cpus` 传递给 Docker，需宿主机已启用 cgroup v2 才能准确限制。
4. **无网络命名空间配置**：`--network=none` 完全禁用网络，但未实现细粒度的网络隔离（如仅允许访问特定 IP）。
5. **无用户命名空间重映射**：容器内 root 映射到宿主 root（未启用 `--userns-remap`），存在容器逃逸提权风险。
6. **`process` 模式无真实隔离**：`runtime=process` 直接在同一进程树中运行，仅用于开发回退，不应在生产使用。
7. **seccomp 文件临时路径**：docker 模式下 seccomp 规则写入临时文件，若 Docker daemon 无法读取该路径会导致启动失败。
8. **工具代码不在容器内**：当前 `tool_wrapper.py` 仅做示意，实际生产需将工具代码打包进容器镜像，或通过卷挂载注入。

---

## 12. 面试谈资（Interview Talking Points）

1. **seccomp 原理**：Linux 内核 seccomp（Secure Computing Mode）通过 BPF 程序过滤系统调用，Docker 的 `--security-opt seccomp=` 在容器启动时向内核注册 seccomp filter。比 iptables 更底层，攻击者即使在容器内获得 root 也无法执行被禁止的 syscall。

2. **gVisor vs seccomp 分层防御**：seccomp 在系统调用层拦截，gVisor 在更高抽象层（用户态内核 Sentry + Gofer）重新实现 syscall 语义。两者组合构成"双重防线"：即使 seccomp 规则有漏洞，gVisor 仍可拦截，反之亦然。

3. **capabilities 最小权限原则**：`--cap-drop=ALL` 移除所有 Linux capabilities（如 CAP_NET_BIND_SERVICE, CAP_SYS_ADMIN），然后按需 `--cap-add` 添加，符合 POLA（Principle of Least Authority）。

4. **资源限制的 cgroup 机制**：`--memory`/`--cpus` 背后是 Linux cgroup v2，通过 memory.max 和 cpu.max 限制容器资源，防止 fork bomb 或内存耗尽攻击（DoS）。

5. **工具注入攻击防御**：当 LLM 生成的 `arguments` 包含 `; rm -rf /` 这类注入时，沙箱通过以下层面阻止：①进程隔离（容器文件系统独立），②只读文件系统（`--read-only`），③seccomp 禁止 `unlink/rmdir`。

6. **异步超时设计**：使用 `asyncio.wait_for` + `proc.kill()` 实现非阻塞超时，避免阻塞事件循环。在 `asyncio.TimeoutError` 时强制终止子进程，防止僵尸进程。

7. **Registry + YAML/JSON 覆盖模式**：档案支持三层配置：代码内置预设 → YAML 文件扩展 → JSON 覆盖（运行时 API 持久化），实现开发/测试/生产环境灵活配置，不需要重启服务。

8. **graceful degradation**：`sandbox_enabled=false` 时工具调用直接执行，不崩溃；`docker not found` 时返回 `exit_code=127` 而非抛异常；配置文件不存在时静默跳过，保证服务可用性。
