# Phase H — Agent 生命周期管理（#39）

> **目标**：为 AgentSpec 添加版本化管理、蓝绿发布、金丝雀灰度与一键回滚能力。  
> 对标「Agent 平台架构全景」中的「Agent 应用层 — 版本控制 & 灰度发布」能力。

---


构建思路、使用链路与逐文件代码说明见 [phase-h-build-and-code-guide.md](./phase-h-build-and-code-guide.md)。
## 1. 设计要点

### 1.1 为何需要 Agent 生命周期管理

| 痛点 | 解决方案 |
|------|---------|
| Agent 配置变更直接生效，出问题无法回退 | 版本快照 + 状态机（draft → active → archived） |
| 无法做灰度验证新版 Agent 效果 | canary / blue_green 流量分配 |
| 线上故障需要紧急回滚 | `rollback_version` 一键恢复 |
| 多环境（dev/staging/prod）Agent 版本不一致 | YAML 配置驱动 + JSON overrides 运行时覆盖 |

### 1.2 版本状态机

```
注册 → [draft] → activate → [active] ←───── rollback
                                │
                   新版本激活 ↓
                           [archived]
                                ↑
                    archive_version（手动）
```

- **draft**：刚注册，尚未上线
- **active**：当前生产版本（每个 agent 最多一个）
- **archived**：历史版本，只读，可审计

### 1.3 发布策略

| 策略 | 说明 | 初始 traffic_split |
|------|------|--------------------|
| `all_at_once` | 全量切换，立即生效 | `{new: 100}` |
| `blue_green` | 蓝绿部署，保留两版本 | `{old: 50, new: 50}` |
| `canary` | 金丝雀，小流量灰度 | `{old: 90, new: 10}` |

### 1.4 与 Multi-Agent 框架集成

- `AgentVersion.spec_snapshot` 存储 `AgentSpec.to_dict()` 快照
- 路由层根据 `RolloutStatus.traffic_split` 决策请求路由到哪个 spec 版本
- 不侵入 `packages/agent/multi_agent/registry.py`，完全独立模块

### 1.5 线程安全

- `threading.RLock` 保护所有读写操作
- 全局单例通过 `threading.Lock` 初始化保护

### 1.6 持久化策略

```
config/agent_versions.yaml          ← git 跟踪的初始版本配置
data/agent_versions_overrides.json  ← admin API 运行时修改（不进 git）
```

写入时序：in-memory 更新 → `_persist()` 写 JSON → 原子性由文件系统保证。

---

## 2. 数据模型

### 2.1 AgentVersion

```python
@dataclass
class AgentVersion:
    version_id: str           # 全局唯一 (uuid4)
    agent_id: str             # 关联的 agent_id
    version: int              # 自增版本号（per agent，从 1 开始）
    spec_snapshot: dict       # AgentSpec.to_dict() 快照（不可变副本）
    created_at: float         # 创建时间戳（Unix epoch）
    created_by: str           # 创建者（tenant_id 或 "system"）
    status: str               # draft | active | archived
    metadata: dict            # 可扩展元数据（tag、描述、CI/CD info 等）
```

### 2.2 RolloutStatus

```python
@dataclass
class RolloutStatus:
    agent_id: str
    active_version: str              # 当前激活的 version_id
    previous_version: str | None     # 上一个激活的 version_id（用于回滚）
    strategy: str                    # RolloutStrategy 值
    traffic_split: dict              # {version_id: percent}，合计 100
    updated_at: float
```

### 2.3 RolloutStrategy

```python
class RolloutStrategy:
    ALL_AT_ONCE = "all_at_once"   # 全量切换
    BLUE_GREEN  = "blue_green"    # 蓝绿部署
    CANARY      = "canary"        # 金丝雀灰度
```

---

## 3. REST API 接口表

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| `POST` | `/internal/agent-lifecycle/{agent_id}/versions` | 注册新版本 | admin |
| `GET` | `/internal/agent-lifecycle/{agent_id}/versions` | 列出该 agent 所有版本 | 所有租户 |
| `GET` | `/internal/agent-lifecycle/versions/{version_id}` | 获取版本详情 | 所有租户 |
| `POST` | `/internal/agent-lifecycle/versions/{version_id}/activate` | 激活版本（含发布策略） | admin |
| `POST` | `/internal/agent-lifecycle/{agent_id}/rollback` | 回滚到前一版本 | admin |
| `GET` | `/internal/agent-lifecycle/{agent_id}/active` | 获取当前激活版本 | 所有租户 |
| `PATCH` | `/internal/agent-lifecycle/{agent_id}/traffic` | 设置流量分配（canary/blue_green） | admin |

### 3.1 请求/响应示例

**注册版本**
```bash
POST /internal/agent-lifecycle/rag_specialist/versions
X-Tenant-Id: t1
Authorization: Bearer admin-token

{
  "spec_snapshot": {"agent_id": "rag_specialist", "name": "RAG 专家 v2"},
  "metadata": {"release_notes": "优化检索精度"}
}
# → 201 AgentVersion JSON
```

**激活版本（canary 灰度）**
```bash
POST /internal/agent-lifecycle/versions/<version_id>/activate
{
  "strategy": "canary"
}
# → 200 RolloutStatus JSON (traffic_split: {old: 90, new: 10})
```

**调整流量**
```bash
PATCH /internal/agent-lifecycle/rag_specialist/traffic
{
  "splits": {"<old_vid>": 50, "<new_vid>": 50}
}
# → 200 RolloutStatus JSON
```

**回滚**
```bash
POST /internal/agent-lifecycle/rag_specialist/rollback
# → 200 RolloutStatus JSON (active_version = previous)
# → 409 NO_PREVIOUS_VERSION 若无可回滚版本
```

---

## 4. 配置表（需添加到 settings.py）

> **注意**：不直接编辑 `apps/gateway/settings.py`，由父 agent 集成。

```python
# Phase H — Agent 生命周期管理 (#39)
agent_lifecycle_enabled: bool = Field(
    default=True,
    validation_alias="AGENT_LIFECYCLE_ENABLED",
    description="启用 Agent 版本管理",
)
agent_lifecycle_versions_path: Path = Field(
    default=REPO_ROOT / "config" / "agent_versions.yaml",
    validation_alias="AGENT_LIFECYCLE_VERSIONS_PATH",
    description="Agent 版本 YAML 配置文件路径",
)
agent_lifecycle_overrides_path: Path = Field(
    default=REPO_ROOT / "data" / "agent_versions_overrides.json",
    validation_alias="AGENT_LIFECYCLE_OVERRIDES_PATH",
    description="Agent 版本 overrides JSON 路径（运行时修改）",
)
```

---

## 5. main.py 集成指令

> **注意**：不直接编辑 `apps/gateway/main.py`，由父 agent 集成。

在 `apps/gateway/main.py` 中添加：

```python
from apps.gateway.agent_lifecycle_routes import router as agent_lifecycle_router

# 在 startup 事件或初始化段：
if settings.agent_lifecycle_enabled:
    from packages.agent.lifecycle import init_lifecycle_registry
    init_lifecycle_registry(
        yaml_path=settings.agent_lifecycle_versions_path,
        overrides_path=settings.agent_lifecycle_overrides_path,
    )

app.include_router(agent_lifecycle_router)
```

---

## 6. .env.example 新增条目

> **注意**：不直接编辑 `.env.example`，由父 agent 集成。

```ini
# Phase H — Agent 生命周期管理 (#39)
AGENT_LIFECYCLE_ENABLED=true
AGENT_LIFECYCLE_VERSIONS_PATH=config/agent_versions.yaml
AGENT_LIFECYCLE_OVERRIDES_PATH=data/agent_versions_overrides.json
```

---

## 7. README 新增章节

> **注意**：不直接编辑 `README.md`，由父 agent 集成。

```markdown
### Phase H — Agent 生命周期管理 (#39)

为 AgentSpec 添加版本化管理、蓝绿/金丝雀灰度发布、一键回滚。

- **版本注册**：`POST /internal/agent-lifecycle/{agent_id}/versions`
- **激活发布**：`POST /internal/agent-lifecycle/versions/{vid}/activate`（支持 `all_at_once`/`blue_green`/`canary`）
- **回滚**：`POST /internal/agent-lifecycle/{agent_id}/rollback`
- **流量控制**：`PATCH /internal/agent-lifecycle/{agent_id}/traffic`

配置：`config/agent_versions.yaml`（默认） + `data/agent_versions_overrides.json`（运行时覆盖）
```

---

## 8. roadmap.md 更新

> **注意**：不直接编辑 `docs/roadmap.md`，由父 agent 集成。

在 Phase H 部分添加：

```markdown
- [x] #39 Agent 生命周期管理（版本化 + 蓝绿/金丝雀 + 回滚）
```

---

## 9. 测试覆盖

| # | 测试函数 | 覆盖点 |
|---|---------|--------|
| 1 | `test_agent_version_dataclass` | 数据模型字段、`to_dict()` |
| 2 | `test_rollout_strategy_constants` | 策略枚举常量、`is_valid()` |
| 3 | `test_register_version_auto_increment` | 版本号自增、跨 agent 隔离 |
| 4 | `test_list_and_get_versions` | 列表排序、全局查找、空 agent |
| 5 | `test_activate_version_all_at_once` | 全量激活、traffic_split、状态转换 |
| 6 | `test_activate_archives_old_and_canary` | 自动归档旧版本、canary 初始分配 |
| 7 | `test_get_active` | 获取激活版本、无激活时 None |
| 8 | `test_rollback_version` | 回滚流程、状态转换验证 |
| 9 | `test_rollback_no_previous` | 边界情况：无前一版本 |
| 10 | `test_archive_version` | 手动归档、幂等性、active 不可归档 |
| 11 | `test_set_traffic_split` | 设置 blue_green 流量分配 |
| 12 | `test_set_traffic_split_validation` | 合计≠100 / 未知 agent / 未知 version |
| 13 | `test_activate_invalid_strategy` | 无效策略抛 `ValueError` |
| 14 | `test_activate_nonexistent_version` | 不存在 version_id 抛 `KeyError` |
| 15 | `test_yaml_load` | YAML 配置加载、active 状态恢复 |
| 16 | `test_json_persist` | JSON 持久化、重新加载验证 |
| 17 | `test_global_singleton` | `init`/`get`/`reset` 三件套 |
| 18 | `test_stats` | 统计指标正确性 |

运行命令：
```bash
python3 tests/test_agent_lifecycle.py
# → 18/18 passed
```

---

## 10. 代码导航

```
packages/agent/lifecycle/
├── __init__.py          # 公开导出：AgentVersion, AgentLifecycleRegistry,
│                        #   init/get/reset_lifecycle_registry, RolloutStrategy, RolloutStatus
└── registry.py          # 核心实现：数据模型 + 注册表 + 全局单例

apps/gateway/
└── agent_lifecycle_routes.py  # FastAPI 路由（prefix: /internal/agent-lifecycle）

tests/
└── test_agent_lifecycle.py    # 18 个测试用例

docs/
└── phase-h-agent-lifecycle.md  # 本文档

config/
└── agent_versions.yaml         # 初始版本配置（需手动创建）

data/
└── agent_versions_overrides.json  # 运行时生成，不进 git
```

---

## 11. 已知限制

1. **traffic_split 仅元数据**：`traffic_split` 目前只存储配置，实际流量路由需在调用层（如 `AgentRunner`）读取 `RolloutStatus` 并按概率分配，本模块不实现路由逻辑。
2. **无原子性保证**：内存更新与 JSON 文件写入非原子操作；多进程部署时需外部分布式锁（如 Redis）。
3. **版本快照不校验**：`spec_snapshot` 接受任意 dict，不强制验证是否符合 `AgentSpec` 结构，调用方负责传入有效快照。
4. **无版本数量上限**：每个 agent 可无限累积历史版本，生产环境建议增加 `max_versions_per_agent` 配置并自动清理最旧归档版本。
5. **回滚仅一级**：`previous_version` 只记录前一版本，不支持回滚两级以上；如需多级回滚，可重复调用 `activate_version`。
6. **无变更审计日志**：版本激活/回滚操作未写入 `audit` 模块，生产环境建议集成 `packages/audit/store.py`。
7. **blue_green 初始 50/50 可能造成流量跌半**：对于关键 agent，建议先设 `canary(10%)` 验证后再全量切换。

---

## 12. 面试要点

1. **为何用 dataclass 而非 Pydantic Model？**  
   dataclass 无 pydantic 依赖，避免 Python 3.9 下 import chain 触发 pydantic v2 兼容性问题；同时 `asdict()` 直接序列化到 JSON，足够满足本场景。

2. **版本号自增的并发安全性？**  
   通过 `threading.RLock` 保护 `register_version`，`max(v.version)+1` 在锁内执行，单进程下线程安全；多进程需改用数据库自增主键。

3. **蓝绿 vs 金丝雀的区别和取舍？**  
   蓝绿：维护两套完整版本，切换成本低但资源占用翻倍；金丝雀：按百分比灰度，能用真实流量验证，风险可控但需持续监控指标。

4. **如何实现零停机发布？**  
   激活新版本时旧版本先归档（状态变更），再更新 `_active` 指针；整个过程在锁内完成，请求层只需在每次处理前读取 `get_active()`，保证新请求立即路由到新版本。

5. **`spec_snapshot` 为何存 dict 而非 AgentSpec 对象？**  
   dict 便于 JSON 序列化/反序列化，避免引入 AgentSpec 类的循环依赖；未来 AgentSpec 结构变更时，旧版本快照仍能完整还原历史状态。

6. **rollback 的幂等性？**  
   回滚后 `previous_version` 置为 `None`，防止重复回滚导致状态异常；若需多次回滚，需显式调用 `activate_version(历史版本id)`。

7. **YAML + JSON override 双层配置的优势？**  
   YAML 进 git，提供可审计的基线配置；JSON override 在运行时由 admin API 写入，不影响 git history，满足"配置即代码"同时支持热更新。
