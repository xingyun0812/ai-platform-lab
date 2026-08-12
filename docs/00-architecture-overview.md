# AI Platform Lab — 系统整体架构图

```mermaid
flowchart TB
    subgraph Client[客户端层]
        SDK[Python SDK]
        CONSOLE[Console Web UI]
    end

    subgraph Gateway[API网关层 - apps/gateway]
        direction TB
        subgraph GW_MID[中间件链]
            AUTH[Auth / OAuth / mTLS]
            RL[Rate Limit / Quota]
            PII[PII 脱敏]
            AUDIT[Audit Log]
        end
        subgraph GW_ROUTES[路由层]
            CHAT[Chat 补全]
            RAG_API[RAG 查询/索引]
            AGENT_API[Agent 运行]
            EMBED[Embedding]
            MCP[MCP 管理]
            BILLING[Billing / 用量]
        end
        subgraph GW_CACHE[缓存]
            SC[Semantic Cache]
        end
    end

    subgraph Worker[异步Worker - apps/worker]
        INDEXER[索引构建工]
    end

    subgraph Packages[能力层 - packages/]
        direction TB
        PKG_RAG[RAG: 分块/Embedding/检索/重排序]
        PKG_AGENT[Agent: ReAct/Planner/CoT/多Agent/Orch]
        PKG_AUTH[Auth: JWT/OAuth2/RBAC/mTLS]
        PKG_MEM[Memory: 长时记忆/摘要]
        PKG_BILL[Billing: 计量/预算]
        PKG_OBS[Observability: OTel/Metrics]
        PKG_PII[PII: 检测/脱敏/安全]
        PKG_HITL[HITL: 人在回路]
        PKG_PROMPT[Prompt: 注册/实验/渲染]
        PKG_SANDBOX[Sandbox: 隔离执行]
        PKG_MCP[MCP: 客户端/注册]
        PKG_EMBED[Embedding: 多模态/模型编排]
        PKG_STORE[Storage: S3/OSS 对象存储]
    end

    subgraph Infra[基础设施]
        PG[(Postgres)]
        RD[(Redis)]
        QD[(Qdrant 向量库)]
        LLM[?? Upstream LLM]
    end

    subgraph Observability[可观测性 - profile:observability]
        OTEL[OpenTelemetry]
        JAEGER[Jaeger 链路]
        PROM[Prometheus]
        GRAFANA[Grafana]
    end

    Client --> Gateway
    Gateway --> Packages
    Gateway --> Worker
    Worker --> Infra
    Packages --> Infra
    Gateway -.-> Observability
    Packages -.-> Observability
```

## 核心流程

| 流程 | 链路 |
|------|------|
| **Chat** | SDK ? Gateway(Auth?RL?PII?Audit?SemCache) ? Provider Router ? Upstream LLM |
| **RAG** | SDK ? Gateway ? Chunker ? Embedding ? Qdrant(hybrid+rerank) ? LLM ? 答案 |
| **Agent** | SDK ? Gateway ? ReAct Loop(LLM?Tool?ACL?HITL?Session) ? 最终响应 |

## 关键架构决策

1. **三ID边界** (ADR-0001): `plan_approval_id` / `execution_id` / `task_id` 永不融合
2. **三层检查点/恢复** (ADR-0002): Plan级 / Orchestrator级 / Long-run级各有独立恢复路径
3. **AppContext单例** (ADR-0003): Gateway用统一容器装配依赖，测试可reset_all
4. **Platform facade**: `packages/` 通过 `PlatformPort` 协议依赖平台，不直接引用 `apps.gateway`
5. **多租户**: Tenant ID + Bearer认证 + 模型白名单 + 工具ACL + 配额/限流
