# Phase F — MCP 真实集成（#32）

> **目标**：将 MCP（Model Context Protocol）从静态 stub 升级为真实协议集成，支持动态加载外部 MCP server 工具。

对标「Agent 平台架构全景」中的「能力中台 — MCP 集成」能力。Phase F 最后一块拼图。

---

## 1. 设计要点

### 1.1 双协议支持

| Transport | 适用场景 | 实现 |
|-----------|---------|------|
| `stdio` | 本地 MCP server（Python/Node 脚本） | 子进程 stdin/stdout，每行一个 JSON-RPC 消息 |
| `http` | 远程 MCP server | HTTP POST + JSON-RPC，可选 SSE 流式 |

### 1.2 MCP 客户端协议

实现 MCP 协议核心三方法：

| 方法 | 说明 |
|------|------|
| `initialize` | 握手；交换 protocolVersion + clientInfo/serverInfo |
| `tools/list` | 列出 server 提供的工具 |
| `tools/call` | 调用工具，返回 content list |

**协议版本**：`2024-11-05`（参考 https://spec.modelcontextprotocol.io/）

### 1.3 数据模型

```python
@dataclass
class MCPServerConfig:
    server_id: str          # 唯一标识
    transport: str           # "stdio" | "http"
    enabled: bool
    # stdio
    command: list[str]
    env: dict[str, str]
    # http
    url: str
    headers: dict[str, str]
    # 鉴权
    api_key: str             # stdio→env；http→Authorization header
    description: str

@dataclass
class MCPServerStatus:
    healthy: bool
    last_check: float
    last_error: str
    tools_count: int

@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict       # JSON Schema
```

### 1.4 存储分层

| 文件 | 用途 | git 跟踪 |
|------|------|---------|
| `config/mcp_servers.yaml` | 默认配置（git 管理） | ✅ |
| `data/mcp_servers_overrides.json` | admin API 运行时修改 | ❌ |

### 1.5 鉴权

| Transport | 鉴权方式 |
|-----------|---------|
| `stdio` | `api_key` 注入到子进程 `env` |
| `http` | `api_key` 注入到 `Authorization: Bearer <key>` header |

### 1.6 工具桥接

MCP 工具自动转换为 Agent `ToolDefinition`：

- **命名**：`mcp_{server_id}_{tool_name}`（避免冲突）
- **描述**：`[MCP:{server_id}] {原描述}`
- **handler**：异步调用 MCP server 的 `tools/call`，返回 content 拼接文本

### 1.7 失败降级

- 单个 MCP server 不可达 → 跳过其工具，不影响其他 server
- 全部 MCP server 不可达 → 回退到内置工具 + `mcp_stub.py`
- `MCP_ENABLED=false` → 完全使用 `mcp_stub.py`

---

## 2. REST API

| Method | Path | 权限 | 说明 |
|--------|------|------|------|
| GET | `/internal/mcp/servers` | 任何已认证 | 列出所有 server |
| GET | `/internal/mcp/servers/{id}` | 任何已认证 | 获取单个详情 |
| POST | `/internal/mcp/servers` | platform_admin | 注册新 server |
| PATCH | `/internal/mcp/servers/{id}` | platform_admin | 更新 |
| DELETE | `/internal/mcp/servers/{id}` | platform_admin | 删除 |
| POST | `/internal/mcp/servers/{id}/test` | platform_admin | 测试连接 |
| GET | `/internal/mcp/servers/{id}/tools` | 任何已认证 | 列出工具 |

### 使用示例

```bash
# 1. 列出 MCP server
curl -s http://127.0.0.1:8000/internal/mcp/servers \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me"

# 2. 注册远程 HTTP MCP server
curl -s -X POST http://127.0.0.1:8000/internal/mcp/servers \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "server_id": "github-mcp",
    "transport": "http",
    "enabled": true,
    "url": "https://mcp.github.com/v1",
    "api_key": "ghp_xxx",
    "description": "GitHub MCP server"
  }'

# 3. 测试连接 + 列出工具
curl -s -X POST http://127.0.0.1:8000/internal/mcp/servers/github-mcp/test \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me"
# → {"connected": true, "tools_count": 5, "tools": [...]}

# 4. 列出工具
curl -s http://127.0.0.1:8000/internal/mcp/servers/github-mcp/tools \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me"

# 5. 删除
curl -s -X DELETE http://127.0.0.1:8000/internal/mcp/servers/github-mcp \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me"
```

---

## 3. 配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `MCP_ENABLED` | `true` | 总开关 |
| `MCP_SERVERS_CONFIG_PATH` | `config/mcp_servers.yaml` | YAML 配置 |
| `MCP_OVERRIDES_PATH` | `data/mcp_servers_overrides.json` | JSON overrides |
| `MCP_CONNECT_TIMEOUT_SECONDS` | `5` | 连接超时 |
| `MCP_TOOL_CALL_TIMEOUT_SECONDS` | `30` | 工具调用超时 |

---

## 4. 集成点

### 4.1 Agent Tool Registry

`packages/agent/registry.py`：
- `get_tool_registry()` — 启动时加载 MCP 工具（同步包装 async）
- `refresh_mcp_tools()` — 运行时刷新（admin API 调用后）

### 4.2 网关启动

`apps/gateway/main.py`：
- `create_app()` 初始化 `MCPServerRegistry`
- 挂载 `/internal/mcp/*` 路由

### 4.3 工具调用流程

```
Agent → tool "mcp_github-mcp_create_issue"
  ↓
ToolRegistry.get() → ToolDefinition（handler 调用 MCPClient）
  ↓
MCPClient.call_tool("create_issue", args)
  ↓
HttpTransport.send_request(JSON-RPC tools/call)
  ↓
MCP server 返回 content list
  ↓
handler 拼接为字符串返回给 Agent
```

---

## 5. 测试与验收

```bash
# 1. 单元测试（9 个用例）
python3 tests/test_mcp.py
# 期望：9/9 passed

# 2. 验收冒烟（PF 段）
python3 eval/acceptance_smoke.py
# 期望：
# PF  MCP transport 构造 + 错误   PASS
# PF  MCP REST API CRUD            PASS
```

---

## 6. 代码导航

```
packages/mcp/
├── __init__.py          # 包导出 + load_mcp_tools + 工具桥接
├── transport.py        # 传输层
│   ├── Transport            # 抽象基类
│   ├── StdioTransport       # 子进程 stdin/stdout
│   ├── HttpTransport        # HTTP POST
│   └── TransportError
├── client.py            # MCP 客户端
│   ├── MCPClient            # JSON-RPC 2.0
│   ├── MCPTool              # 工具描述
│   ├── MCPServerInfo        # server 信息
│   └── MCPClientError
└── registry.py          # server 注册表
    ├── MCPServerConfig      # 配置数据类
    ├── MCPServerStatus      # 健康状态
    ├── MCPServerRegistry    # 注册表（YAML + JSON overrides）
    ├── init_mcp_registry()  # 全局单例
    └── get_mcp_registry()

apps/gateway/mcp_routes.py        # REST API
packages/agent/registry.py          # Agent 集成（动态加载）
config/mcp_servers.yaml             # YAML 默认（示例）
```

---

## 7. 已知限制（面试时主动说）

1. **无连接池**：每次 `call_tool` 都复用同一 transport，但 stdio 子进程长期存活；生产应做连接池管理。
2. **无 SSE 流式**：`HttpTransport` 仅支持 POST/JSON；SSE 流式响应未实现（MCP spec 可选）。
3. **健康检查被动**：`mark_healthy`/`mark_unhealthy` 仅在工具加载或测试连接时触发；无主动心跳。
4. **无工具级鉴权**：当前 server 级 `api_key`；工具级 ACL 需扩展。
5. **启动时阻塞**：`get_tool_registry()` 同步加载所有 MCP server，启动慢；应改为 lazy 加载。
6. **stub 保留**：`mcp_stub.py` 未删除，作为 `MCP_ENABLED=false` 时的兜底。

---

## 8. 后续演进

| Issue | 内容 | 依赖 |
|-------|------|------|
| 未来 | 连接池 + 心跳 | — |
| 未来 | SSE 流式响应 | — |
| 未来 | 工具级 ACL | RBAC 扩展 |
| 未来 | Lazy 加载（首次调用时连接） | — |
| 未来 | MCP server 沙箱隔离（容器级） | #44 |

---

## 9. 面试讲法

1. **为什么需要 MCP**：内置工具有限，外部工具（GitHub/Jira/Slack）通过 MCP 标准协议接入，解耦工具实现与平台。
2. **双协议**：stdio 本地（低延迟、安全）/ http 远程（跨网络、可扩展），覆盖不同部署场景。
3. **失败降级**：单 server 失败不影响其他；全部失败回退 stub；保证可用性。
4. **工具桥接**：MCP 工具自动转 `ToolDefinition`，对 Agent 透明；命名加前缀避免冲突。
5. **诚实边界**：无连接池、无 SSE 流式、启动时阻塞加载；这些是生产化需补的点。

参考代码：
- `packages/mcp/transport.py:40` — StdioTransport
- `packages/mcp/transport.py:140` — HttpTransport
- `packages/mcp/client.py:60` — MCPClient
- `packages/mcp/registry.py:90` — MCPServerRegistry
- `apps/gateway/mcp_routes.py` — REST API
- `packages/agent/registry.py:95` — Agent 集成
