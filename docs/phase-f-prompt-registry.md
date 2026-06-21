# Phase F — Prompt 版本化（#29）

> **目标**：将 prompt 从静态 txt 文件升级为版本化、可管理、可审计的资产，支持灰度切换、回滚。

对标「Agent 平台架构全景」中的「能力中台 — Prompt 管理」能力。这是 **#30 A/B 测试**和 **#33 上下文压缩**的基础。

---

## 1. 设计要点

### 1.1 数据模型

```python
@dataclass
class PromptVersion:
    prompt_id: str          # "rag_query", "agent_system"
    version: int           # 1, 2, 3...
    content: str           # 模板内容，支持 {{var}} 占位符
    variables: list[str]   # 自动从 content 解析
    status: str            # "draft" | "active" | "archived"
    tenant_id: str = "global"  # 多租户隔离
    changelog: str         # 版本变更说明
    created_at: float
    created_by: str        # 审计字段
```

**状态机**：
- `draft` → 创建后默认（不可被 `get_active` 选中）
- `active` → 当前生效版本（同 prompt_id 仅一个）
- `archived` → 历史版本（可查询，不可激活）

创建新 active 时，旧 active 自动转为 archived。

### 1.2 模板语法

使用 **`{{var}}` 双花括号**（Jinja 风格）：

```
参考资料：{{context}}
用户问题：{{query}}
```

**为什么不选其他语法**：
- `str.format()` 的 `{var}`：与 RAG prompt 中的 `{context}`/`{query}` 冲突
- `string.Template` 的 `$var`：不直观，且易与 shell 变量混淆
- `{{var}}`：清晰、安全、与 OpenAI/LangChain 生态一致

### 1.3 存储分层

| 层 | 文件 | 用途 | git 跟踪 |
|----|------|------|---------|
| **YAML 默认** | `config/prompts.yaml` | 初始版本，git 管理 | ✅ |
| **JSON overrides** | `data/prompt_overrides.json` | admin API 运行时修改 | ❌ |

启动时合并：YAML + JSON overrides。所有写入仅落 JSON（不污染 git）。

### 1.4 向后兼容

若 `prompt_id` 在 registry 中不存在，自动回退到 legacy txt 文件：

```python
init_registry(
    yaml_path=settings.prompts_config_path,
    overrides_path=settings.prompt_overrides_path,
    legacy_fallback={
        "rag_query": settings.rag_prompt_path,  # 若 yaml 中无此 id，回退 txt
    },
)
```

### 1.5 多租户隔离

按 `tenant_id` 分桶，每个租户可有独立 prompt 版本。当前默认 `tenant_id="global"`。

---

## 2. REST API

| Method | Path | 权限 | 说明 |
|--------|------|------|------|
| GET | `/internal/prompts` | 任何已认证 | 列出所有 prompt_id + stats |
| GET | `/internal/prompts/{prompt_id}` | 任何已认证 | 获取 active 版本 |
| GET | `/internal/prompts/{prompt_id}/versions` | 任何已认证 | 列出所有版本 |
| POST | `/internal/prompts/{prompt_id}/versions` | platform_admin | 创建新版本（默认 set_active=true） |
| PATCH | `/internal/prompts/{prompt_id}/active` | platform_admin | 切换 active 版本 |
| POST | `/internal/prompts/{prompt_id}/render` | 任何已认证 | 渲染模板（传入变量） |

### 使用示例

```bash
# 1. 列出所有 prompts
curl -s http://127.0.0.1:8000/internal/prompts \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me"
# → {"prompt_ids": ["agent_kb_hint", "rag_query", "rag_system"], "stats": {...}}

# 2. 创建 rag_query v2（自动 set_active）
curl -s -X POST http://127.0.0.1:8000/internal/prompts/rag_query/versions \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "新模板 参考资料：{{context}}\n问题：{{query}}",
    "changelog": "v2 实验版本",
    "set_active": true
  }'

# 3. 回滚到 v1
curl -s -X PATCH http://127.0.0.1:8000/internal/prompts/rag_query/active \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{"version": 1}'

# 4. 预览渲染
curl -s -X POST http://127.0.0.1:8000/internal/prompts/rag_query/render \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{"variables": {"context": "[CTX]", "query": "什么是 RAG?"}}'
```

---

## 3. 配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `PROMPT_REGISTRY_ENABLED` | `true` | 总开关 |
| `PROMPTS_CONFIG_PATH` | `config/prompts.yaml` | YAML 默认路径 |
| `PROMPT_OVERRIDES_PATH` | `data/prompt_overrides.json` | JSON overrides 路径 |

---

## 4. 集成点

### 4.1 RAG Query

`apps/gateway/rag/query_service.py`：
- `_resolve_rag_prompt_template()` — 优先取 `rag_query` active 版本；回退 legacy txt
- `_resolve_rag_system_prompt()` — 优先取 `rag_system`；回退硬编码

### 4.2 Agent Routes

`apps/gateway/agent/routes.py`：
- `_resolve_agent_kb_hint()` — 优先取 `agent_kb_hint` 模板渲染；回退硬编码

### 4.3 网关启动

`apps/gateway/main.py`：
- `create_app()` 启动时初始化 prompt registry
- 挂载 `/internal/prompts/*` 路由

---

## 5. 测试与验收

```bash
# 1. 单元测试（18 个用例）
python3 tests/test_prompt_registry.py
# 期望：18/18 passed

# 2. 验收冒烟（PF 段）
python3 eval/acceptance_smoke.py
# 期望：
# PF  Prompt 模板渲染 + 变量提取   PASS
# PF  Prompt API list/get/render    PASS
# PF  Prompt 创建版本 + 回滚         PASS
```

---

## 6. 代码导航

```
packages/prompt/
├── __init__.py          # 包导出
├── render.py            # {{var}} 模板渲染
│   ├── extract_variables()
│   ├── render()
│   └── validate_template()
└── registry.py          # PromptRegistry
    ├── PromptVersion        # 数据类
    ├── PromptRegistry       # 注册表
    │   ├── load()              # 加载 YAML + JSON
    │   ├── list_prompt_ids()
    │   ├── list_versions()
    │   ├── get_active()         # 获取 active 版本
    │   ├── get_version()        # 获取指定版本
    │   ├── render()             # 渲染 active
    │   ├── create_version()     # 创建新版本（落 JSON）
    │   ├── set_active()         # 切换 active（落 JSON）
    │   ├── archive_version()
    │   └── _legacy_fallback_get()  # 向后兼容
    ├── init_registry()       # 全局单例
    └── get_registry()

apps/gateway/prompt_routes.py   # REST API
config/prompts.yaml             # YAML 默认
data/prompt_overrides.json      # JSON overrides（git ignore）
```

---

## 7. 已知限制（面试时主动说）

1. **无 A/B 测试流量分桶**：当前只能手动切换 active，未实现按流量比例分流（属 #30 范畴）。
2. **无审计日志**：`created_by` 记录了操作者，但无完整审计链路（谁/何时/改了什么）。后续可接 Postgres audit 表。
3. **单进程内存缓存**：`PromptRegistry` 是进程内单例；多实例部署时，admin 写入后其他实例需重启或加 TTL 刷新。后续可改为 Redis 共享存储。
4. **无审批流**：创建/切换 active 不需要审批，admin 可直接操作。生产场景应加 PR 审批流程。
5. **租户级覆盖未启用**：`tenant_id` 字段已支持，但 API 仅暴露 global 范围。后续可加 `?tenant_id=demo-a` 查询参数。

---

## 8. 后续演进

| Issue | 内容 | 依赖 |
|-------|------|------|
| #30 | Prompt A/B 测试：流量分桶 + 指标对比 + 自动胜出 | #29 ✅ |
| #33 | 上下文压缩策略：滑窗 + LLM 摘要 + Token 感知注入 | — |
| 未来 | Redis 共享存储（多实例一致） | — |
| 未来 | 审批流 + 审计链路 | — |
| 未来 | 租户级覆盖 API | — |

---

## 9. 面试讲法

1. **为什么需要版本化**：Prompt 是 LLM 应用的核心资产，迭代频繁；静态 txt 无法追踪变更、无法回滚、无法 A/B 测试。
2. **双存储分层**：YAML 进 git（团队协作），JSON overrides 运行时修改（admin 灵活），互不污染。
3. **状态机设计**：draft → active → archived，保证同一时刻只有一个 active，切换自动归档旧版本。
4. **向后兼容**：legacy txt 自动回退，存量 RAG 流程零改动即可受益。
5. **REST API**：创建/切换/渲染全流程 API 化，未来可对接 Console UI。
6. **诚实边界**：当前无 A/B 流量分桶（属 #30）；多实例共享存储待 Redis 化。

参考代码：
- `packages/prompt/registry.py:55` — PromptVersion 数据类
- `packages/prompt/registry.py:170` — PromptRegistry
- `packages/prompt/render.py:24` — render 函数
- `apps/gateway/prompt_routes.py:90` — POST /versions API
- `apps/gateway/rag/query_service.py:22` — RAG 集成
- `config/prompts.yaml` — 初始种子数据
