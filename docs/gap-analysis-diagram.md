# Gap Analysis — ai-platform-lab vs 目标架构全景

> 对比「Agent 平台架构全景 × AgentOps 治理体系」图，梳理现状与缺口。  
> **更新**：Phase A～K 已交付；本节反映 **2026-06 Phase L Wave1** 基线（~85% 模块覆盖，深度仍待 L1～L3）。

> 图例：✅ 已实现  🟡 部分实现 / opt-in  🟠 stub / 待深化  ❌ 缺失

```mermaid
flowchart TB
    subgraph LEGEND["图例"]
        L1["✅ 已实现"]
        L2["🟡 部分实现"]
        L3["🟠 stub/待深化"]
        L4["❌ 缺失"]
    end

    subgraph APP["Agent 应用层"]
        A1["✅ Agent 核心循环\n感知→规划→决策→行动→观察"]
        A2["🟡 控制流编排\nOrchestrator ✅\n复杂 DAG/可视化 ❌"]
        A3["🟡 Agent 生命周期\n注册/灰度 API ✅\n生产级发布流 ❌"]
        A4["🟡 Multi-Agent\n委托/通信 ✅\n大规模协作治理 ❌"]
        A5["🟡 Agent 运行时\n限流·熔断·超时 ✅\n沙箱默认关·动态市场 ❌"]
        A6["🟡 人机协同 HITL\n审批 REST ✅\nConsole Vertical 演示 #59"]
        A7["🟡 开发者工具\nPython SDK + Console V2 ✅\nPlayground/PyPI ❌"]
    end

    subgraph CAP["能力中台"]
        C1["🟡 能力中心\n内置Tools + MCP桥接 ✅\n公开市场生态 ❌"]
        C2["🟡 长记忆管理\nRedis Session + Memory Store ✅\n跨租户治理浅"]
        C3["🟡 Prompt 管理\n版本化/A-B/审计 ✅\nConsole 编辑流浅"]
        C4["🟡 知识库\nhybrid+金丝雀 ✅\nrerank stub #54"]
        C5["🟡 上下文管理\ncontext_budget+压缩 ✅\n策略矩阵不全"]
    end

    subgraph MODEL["模型服务层"]
        M1["✅ LLM Gateway\n协议统一·负载均衡·降级"]
        M2["✅ 模型路由\n大小模型调度·成本权衡·熔断"]
        M3["🟡 语义缓存\nexact/embedding ✅\n默认关·调优有限"]
        M4["🟡 Embedding 服务\n调用+独立路由 ✅\n多租户计量浅"]
        M5["✅ 模型管理\n版本·基准评测·上下线"]
    end

    subgraph INFRA["基础设施层"]
        I1["🟡 沙箱执行环境\n工具隔离 API ✅\ngVisor/容器默认关"]
        I2["✅ 向量数据库\nQdrant·索引优化"]
        I3["🟡 对象存储\nlocal/s3/oss ✅\n生产桶策略未验"]
        I4["🟡 计算资源\nCompose+Helm ✅\nGPU/多AZ 模板级"]
        I5["✅ 环境管理\nDev/Staging/Prod Compose"]
    end

    subgraph OPS["AgentOps 治理体系"]
        O1["✅ 可观测性\nOTel+Jaeger+Prometheus+Grafana"]
        O2["🟡 评测体系\nModel/Agent Eval ✅\nLLM Judge #56·飞轮 live #61"]
        O3["🟡 安全合规\nPII+OAuth2/mTLS ✅\nDLP/SIEM 级 ❌"]
        O4["✅ 成本管控\nToken计量·预算·告警·路由优化"]
    end

    APP --> CAP --> MODEL --> INFRA
    OPS -.贯穿全层.-> APP
    OPS -.贯穿全层.-> CAP
    OPS -.贯穿全层.-> MODEL
```

## 完成度汇总

| 层次 | 完成度 | 强项 | 主要缺口（Phase L 目标） |
|------|--------|------|-------------------------|
| 模型服务层 | ~90% | Gateway、路由、熔断、语义缓存、计费 | Embedding 独立治理、缓存生产调优 |
| 基础设施层 | ~75% | Qdrant、Compose、Helm、对象存储 | 多 AZ/GPU **实际部署验证** |
| 能力中台 | ~85% | RAG 版本化、Prompt A/B、MCP、Memory | **真 Rerank**、增量索引 |
| Agent 应用层 | ~80% | 核心循环、Orchestrator、Multi-Agent、HITL | Vertical 演示链、三率指标 |
| AgentOps 治理 | ~80% | 可观测、成本、PII、分级审计 | **LLM Judge**、反馈飞轮 live、SLO |

**整体**：模块清单 **~88%**；「能讲数字、能演示发版 SOP」约 **~60%**（Phase L 进行中）。

## 与 roadmap 一致性

| 能力 | gap-analysis | roadmap §已知限制 |
|------|--------------|-------------------|
| MCP | 🟡 桥接已有，非市场 | Agent 节一致 |
| HITL | 🟡 REST 已有 | Agent 节一致 |
| 语义缓存 | 🟡 opt-in | 模型服务节一致 |
| PII | 🟡 规则级 | 安全节一致 |
| Rerank | 🟠 stub | RAG 节 #54 |
| 反馈飞轮 | 🟡 代码有、live 无 | 评测节 #61 |

详见 [phase-l-priority-roi.md](./phase-l-priority-roi.md)。
