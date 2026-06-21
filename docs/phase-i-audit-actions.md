# Phase I — 动作分级审计（Issue #42）

> **目标**：为工具调用增加动作级别分类（read_only / write / destructive / network / unknown），审计日志包含动作级别，destructive 动作与 HITL (#40) 审批机制集成，并提供 REST API 查询/过滤。

对标「AI Agent 安全与合规」中的「工具操作审计与权限分级」能力。

---

## 1. 设计要点

### 1.1 动作分级模型

| 级别 | 值 | 说明 | 示例工具 |
|------|----|------|---------|
| `read_only` | `"read_only"` | 仅读取，无副作用 | `calc`, `get_kb_snippet`, `list_users` |
| `write` | `"write"` | 写入/修改，可逆 | `create_record`, `update_profile`, `send_email` |
| `destructive` | `"destructive"` | 不可逆高危操作 | `delete_file`, `drop_table`, `rm_dir` |
| `network` | `"network"` | 出站网络请求 | `search_web_stub`, `httpbin_delay`, `webhook_call` |
| `unknown` | `"unknown"` | 未知/未分类 | 新工具、未注册工具 |

### 1.2 分类策略

优先级：**注册表 > 启发式**

**启发式关键字匹配**（工具名小写包含以下关键字）：

| 级别 | 关键字 |
|------|--------|
| destructive | delete, drop, rm, destroy, purge, truncate, remove |
| network | http, request, webhook, ping, download, crawl, scrape |
| write | create, update, write, send, put, post, insert, save, upload, set |
| read_only | get, list, read, search, fetch, query, find, show, describe, check |
| unknown | 无匹配 |

### 1.3 审批联动（与 HITL #40 集成）

- `ActionClassifier.requires_approval(tool_name)` → 当 `action_level == destructive` 或 `requires_approval=True` 时返回 `True`
- 调用方（orchestrator/agent）在执行前检查此标志，若 True 则创建 HITL 审批请求
- 审计记录中 `approval_id` 字段指向对应的 HITL 审批请求

### 1.4 降级策略

- `get_classifier()` 返回 `None` → REST 层返回 503；不影响其他模块
- YAML/JSON 文件不存在/损坏 → 静默跳过（graceful degradation），使用内置默认分类
- `get_action_logger()` 返回 `None` → REST 层返回 503；日志记录不阻塞主流程

---

## 2. 数据模型

### 2.1 ToolActionClassification

```python
@dataclass
class ToolActionClassification:
    tool_name: str           # 工具唯一名称
    action_level: str        # ActionLevel 常量
    requires_approval: bool  # 是否强制审批（默认 False）
    description: str         # 描述
    metadata: dict           # 扩展字段
```

### 2.2 ActionAuditEntry

```python
@dataclass
class ActionAuditEntry:
    entry_id: str            # UUID
    tenant_id: str           # 租户 ID
    session_id: str          # 会话 ID
    tool_name: str           # 工具名
    action_level: str        # ActionLevel
    arguments: dict          # 工具调用参数
    result_summary: str      # 结果摘要（截断）
    status: str              # success | failed | denied | pending
    created_at: float        # Unix timestamp
    decided_by: str | None   # 审批人（HITL 决策）
    approval_id: str | None  # HITL 审批请求 ID
```

---

## 3. REST API

### 前缀：`/internal/audit-actions`

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| `GET` | `/classifications` | 列出所有工具分类 | 普通用户 |
| `GET` | `/classifications/{tool_name}` | 获取单个工具分类 | 普通用户 |
| `POST` | `/classifications` | 注册新分类 | admin |
| `PATCH` | `/classifications/{tool_name}` | 更新分类 | admin |
| `DELETE` | `/classifications/{tool_name}` | 删除分类 | admin |
| `POST` | `/classify` | 分类工具调用，返回 action_level + requires_approval | 普通用户 |
| `GET` | `/actions` | 列出审计记录（`?tenant_id=&action_level=&limit=`）| 普通用户 |
| `GET` | `/actions/destructive` | 列出 destructive 记录（`?tenant_id=&limit=`）| 普通用户 |
| `GET` | `/actions/{entry_id}` | 获取审计记录详情 | 普通用户 |

#### POST /classify 示例

请求：
```json
{"tool_name": "delete_user", "arguments": {"user_id": "u-001"}}
```

响应：
```json
{
  "tool_name": "delete_user",
  "action_level": "destructive",
  "requires_approval": true,
  "source": "heuristic"
}
```

#### GET /actions 查询参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `tenant_id` | string | 按租户过滤（默认当前 tenant） |
| `action_level` | string | 按级别过滤（可选） |
| `limit` | int | 最多返回条数（默认 50，上限 500）|

---

## 4. 配置表

### settings.py 需新增字段

| 字段名 | 环境变量 | 默认值 | 说明 |
|--------|---------|--------|------|
| `audit_actions_enabled` | `AUDIT_ACTIONS_ENABLED` | `True` | 启用动作分级审计 |
| `audit_actions_config_path` | `AUDIT_ACTIONS_CONFIG_PATH` | `REPO_ROOT/config/tool_classifications.yaml` | 工具分类 YAML 配置 |
| `audit_actions_overrides_path` | `AUDIT_ACTIONS_OVERRIDES_PATH` | `REPO_ROOT/data/tool_classifications_overrides.json` | 运行时覆盖 JSON |
| `audit_actions_store_database_url` | `AUDIT_ACTIONS_STORE_DATABASE_URL` | `None` | 动作审计存储 URL；None=内存 |
| `audit_destructive_requires_approval` | `AUDIT_DESTRUCTIVE_REQUIRES_APPROVAL` | `True` | destructive 动作是否强制审批 |

### settings.py 字段定义（待集成人员添加）

```python
audit_actions_enabled: bool = Field(
    default=True,
    validation_alias="AUDIT_ACTIONS_ENABLED",
    description="启用动作分级审计",
)
audit_actions_config_path: Path = Field(
    default=REPO_ROOT / "config" / "tool_classifications.yaml",
    validation_alias="AUDIT_ACTIONS_CONFIG_PATH",
)
audit_actions_overrides_path: Path = Field(
    default=REPO_ROOT / "data" / "tool_classifications_overrides.json",
    validation_alias="AUDIT_ACTIONS_OVERRIDES_PATH",
)
audit_actions_store_database_url: str | None = Field(
    default=None,
    validation_alias="AUDIT_ACTIONS_STORE_DATABASE_URL",
    description="动作审计存储；None=内存",
)
audit_destructive_requires_approval: bool = Field(
    default=True,
    validation_alias="AUDIT_DESTRUCTIVE_REQUIRES_APPROVAL",
    description="destructive 动作是否强制要求审批",
)
```

---

## 5. main.py 集成说明

### 集成代码（待集成人员在 main.py 添加）

```python
# 在 router 注册区域添加：
from apps.gateway.audit_action_routes import router as audit_action_router

# 在 startup/init 区域添加：
if settings.audit_actions_enabled:
    from packages.audit.action_levels import init_classifier
    from packages.audit.action_logger import init_action_logger
    init_classifier(
        yaml_path=settings.audit_actions_config_path,
        overrides_path=settings.audit_actions_overrides_path,
    )
    init_action_logger(database_url=settings.audit_actions_store_database_url)

app.include_router(audit_action_router)
```

---

## 6. .env.example 新增变量

```dotenv
# --- Phase I: 动作分级审计 ---
AUDIT_ACTIONS_ENABLED=true
AUDIT_ACTIONS_CONFIG_PATH=config/tool_classifications.yaml
AUDIT_ACTIONS_OVERRIDES_PATH=data/tool_classifications_overrides.json
AUDIT_ACTIONS_STORE_DATABASE_URL=
AUDIT_DESTRUCTIVE_REQUIRES_APPROVAL=true
```

---

## 7. README 新增章节

```markdown
### Phase I — 动作分级审计 (#42)

工具调用动作分级审计，支持 read_only / write / destructive / network / unknown 五级分类。
- 内置启发式分类（基于工具名关键字）
- YAML/JSON 注册表覆盖
- destructive 动作联动 HITL 审批
- REST API: `/internal/audit-actions/`
```

---

## 8. docs/roadmap.md 更新说明

在 Phase I 行添加：

```markdown
| I | #42 | 动作分级审计 | packages/audit/action_levels.py, packages/audit/action_logger.py, apps/gateway/audit_action_routes.py | ✅ |
```

---

## 9. 测试

### 运行

```bash
python3 tests/test_audit_actions.py
```

### 测试覆盖（15 个测试用例）

| # | 测试名 | 覆盖点 |
|---|--------|--------|
| 1 | `test_action_level_constants` | ActionLevel 所有常量值 + is_valid |
| 2 | `test_tool_action_classification_dataclass` | ToolActionClassification 字段 + to_dict |
| 3 | `test_classifier_register_get_list` | register + get + list_classifications |
| 4 | `test_heuristic_destructive` | 启发式 → destructive |
| 5 | `test_heuristic_write` | 启发式 → write |
| 6 | `test_heuristic_read_only` | 启发式 → read_only |
| 7 | `test_requires_approval` | destructive/requires_approval 标志 |
| 8 | `test_builtin_classifications` | 5 个内置默认分类 |
| 9 | `test_yaml_load` | YAML 文件加载 |
| 10 | `test_json_overrides` | JSON 覆盖加载 |
| 11 | `test_action_audit_logger_basic` | log_action + get_action + list_actions |
| 12 | `test_list_destructive_actions` | list_destructive_actions 过滤 |
| 13 | `test_list_actions_filter` | list_actions 按 action_level 过滤 |
| 14 | `test_singleton_lifecycle` | init / get / reset 单例全生命周期 |
| 15 | `test_remove_classification` | remove_classification + 幂等性 |

---

## 10. 代码导航

| 文件 | 职责 |
|------|------|
| `packages/audit/action_levels.py` | ActionLevel 常量、ToolActionClassification 数据模型、ActionClassifier（注册表 + 启发式 + YAML/JSON 加载）、全局单例 |
| `packages/audit/action_logger.py` | ActionAuditEntry 数据模型、ActionAuditLogger（内存存储）、全局单例 |
| `packages/audit/store.py` | 原有 HTTP 审计存储（SQLite），本 Phase 不修改 |
| `apps/gateway/audit_action_routes.py` | FastAPI 路由，前缀 `/internal/audit-actions` |
| `config/tool_classifications.yaml` | 内置工具分类 YAML 配置 |
| `tests/test_audit_actions.py` | 15 个单元测试，importlib 隔离加载 |

---

## 11. 已知限制

1. **内存存储无持久化**：`ActionAuditLogger` 当前使用内存 dict，服务重启后审计日志丢失；`database_url` 字段预留但未实现 SQLite 后端。
2. **HITL 联动未自动触发**：`requires_approval()` 返回 `True` 后需调用方显式创建 HITL 请求，本 Phase 未内嵌自动拦截逻辑。
3. **arguments 无截断**：审计条目存储原始 arguments，大型 payload 可能导致内存占用过高；建议生产环境添加截断。
4. **启发式精度有限**：关键字匹配基于工具名，无法感知动态行为；同名工具不同参数可能对应不同风险级别。
5. **无分页**：`list_actions` / `list_destructive_actions` 仅支持 `limit` 截断，无 cursor/offset 分页，大量记录时性能下降。
6. **action_level 覆盖无版本控制**：JSON 覆盖文件直接替换，无 diff 或回滚机制。
7. **单例初始化不可重配置**：`init_classifier` / `init_action_logger` 只在第一次调用时生效，服务运行期间无法热更新配置。

---

## 12. 面试谈话要点

1. **为什么需要动作分级？**  
   工具调用的副作用差异巨大：`get_user` 无风险，`drop_table` 不可逆。分级使安全策略可以精准作用于高风险操作，而不是一刀切拒绝所有工具调用。

2. **启发式分类 vs 注册表，如何取舍？**  
   注册表提供精确控制（可覆盖），启发式作为 fallback 保证新工具的基线安全（宁可误判为 destructive 也不漏判）。两层机制互补。

3. **Thread-safe 注册表设计**  
   使用 `threading.RLock` 而非 `threading.Lock`，因为 `register_classification` 可能在持锁时被回调调用（可重入需求）。所有公开方法只在最短临界区内持锁。

4. **Graceful Degradation 如何实现？**  
   YAML/JSON 加载失败时静默 `pass`，回退到内置默认分类；`get_classifier()` 返回 `None` 时 REST 层返回 503 而非崩溃，不影响其他路由的正常服务。

5. **与 HITL (#40) 的集成边界**  
   `requires_approval()` 是纯查询接口，不主动触发审批流程。调用层（orchestrator/agent）负责检查并创建 HITL 请求，实现关注点分离（分类与流程控制解耦）。

6. **如何扩展到 SQLite/Postgres 持久化？**  
   `ActionAuditLogger.__init__` 接收 `database_url` 参数，当前实例化内存 `_InMemoryActionStore`；只需在判断 `database_url` 非空时替换为 `SqliteActionStore` 或 `PostgresActionStore`，外部接口不变（适配器模式）。

7. **为什么使用 importlib 隔离加载测试？**  
   项目依赖 pydantic v2，`packages/agent/__init__.py` 的模块链在 Python 3.9 下需要 pydantic 环境。通过 `importlib.util.spec_from_file_location` 直接加载文件，绕过包初始化链，使单元测试可在无完整依赖环境下运行。

8. **ActionLevel 为何不用 Python StrEnum？**  
   `StrEnum` 在 Python 3.11+ 才进入标准库，为保持 Python 3.9 兼容性，使用普通类常量 + `_ALL` 集合校验，行为等价于 StrEnum 且兼容性更好。
