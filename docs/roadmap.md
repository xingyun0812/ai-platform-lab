# 路线图与已知限制

本仓库目标是 **6 周学习闭环 + 面试可讲**，以下诚实列出「还没做」与「若上生产还需补什么」。

---

## 当前已完成（Phase A～E）

| 模块 | 状态 | 说明 |
|------|------|------|
| 多租户 Gateway | ✅ | 鉴权、日配额、trace_id |
| RAG 管道 | ✅ | 异步索引、kb 版本、Qdrant |
| RAG 问答 | ✅ | min_score 拒答、citations、baseline |
| Agent 运行时 | ✅ | 工具白名单、session、tool_trace |
| 观测与评测 | ✅ | OTel span、/metrics、eval/run.py |
| 硬化（第 6 周） | ✅ | Model Router、令牌桶、Compose 一键起 |
| 可内测（Phase A） | ✅ | Redis 共享配额/限流、Worker 队列、SQLite 审计、CI |
| Token 计量与预算（Phase B1）| ✅ | Postgres token 计量、租户预算、billing API |
| 密钥/混合检索/可观测（Phase B2）| ✅ | 密钥 Env/Vault、RAG hybrid、OTel Collector |
| rerank + kb 金丝雀（Phase B3）| ✅ | RAG rerank stub、kb 版本金丝雀路由 |
| 平台化（Phase C） | ✅ | 供应商矩阵、Region、租户 API、工具市场 |
| 运维/治理/控制台（Phase D）| ✅ | 熔断/Grafana、JWT/RBAC、控制台、账单 API |
| Agent 效果深化（Phase E） | ✅ | 轨迹评测、意图路由、Session 滚动摘要、HITL stub |

---

## Gap Analysis — 对标「Agent 平台架构全景」

> 参见完整对比图：[gap-analysis-diagram.md](./gap-analysis-diagram.md)  
> 甘特图：[roadmap-gantt.md](./roadmap-gantt.md)

### 完成度汇总

| 层次 | 完成度 | 强项 | 主要缺口 |
|------|--------|------|---------|
| 模型服务层 | ~95% | Gateway、路由、熔断、计费、**语义缓存 ✅**、**Embedding 独立服务 ✅** | 多模态 Embedding |
| 基础设施层 | ~60% | Qdrant、Compose 环境管理 | 云原生、对象存储、GPU 调度 |
| 能力中台 | ~90% | RAG 完整、**Prompt 版本化+A/B ✅**、**长记忆 ✅**、**上下文压缩 ✅**、**MCP ✅** | — |
| Agent 应用层 | ~90% | 核心循环、**控制流编排 ✅**、**Multi-Agent ✅**、**Agent 生命周期 ✅**、**HITL 完整 ✅** | — |
| AgentOps 治理 | ~80% | 可观测、成本管控、**沙箱 ✅**、**分级审计 ✅**、**PII 脱敏 ✅**、**OAuth2/mTLS ✅** | 在线评测飞轮 |

### 核心缺口（完全缺失）

1. **Multi-Agent 协作框架** — 多 Agent 协作·委托·通信
2. **控制流编排引擎** — DAG/分支/循环/子任务调度
3. **语义缓存** — 相似请求 Embedding 复用，成本管控飞轮关键一环
4. **开发者工具层** — SDK / API / Playground 完全空白
5. **Agent 生命周期管理** — 灰度发布、蓝绿切换、版本回滚

### 核心缺口（部分实现需补全）

1. ~~**Prompt 管理** — 只有静态 txt，缺版本化/A-B 测试/回滚/审计~~ ✅ #29 + #30 已完成
2. ~~**长记忆持久化** — Redis Session 只在单会话内，缺跨 Session 持久化~~ ✅ #31 已完成
3. ~~**MCP 完整集成** — 只有 `mcp_stub.py`，缺真实协议集成~~ ✅ #32 已完成
4. ~~**上下文压缩** — 缺 LLM 摘要 + Token 感知注入~~ ✅ #33 已完成
5. ~~**控制流编排** — 缺 DAG/分支/循环引擎~~ ✅ #37 已完成
6. ~~**Multi-Agent 协作** — 缺主从委托 + 并行 + 监督~~ ✅ #38 已完成
7. ~~**Agent 生命周期** — 缺版本管理 + 灰度发布 + 回滚~~ ✅ #39 已完成
8. ~~**HITL 完整工作流** — `hitl.py` 是 stub，缺审批/上报/监督完整链路~~ ✅ #40 已完成
9. ~~**Embedding 独立服务** — 内联在 RAG，缺独立微服务~~ ✅ #35 已完成
10. ~~**沙箱容器隔离** — 工具调用无 gVisor/seccomp 级别隔离~~ ✅ #41 已完成
11. ~~**动作分级审计** — 缺 read-only/write/destructive 分级~~ ✅ #42 已完成
12. ~~**PII 脱敏** — 缺 PII 检测 + 脱敏 API~~ ✅ #43 已完成
13. ~~**生产级鉴权** — 仅 JWT HS256，缺 OAuth2/mTLS~~ ✅ #44 已完成
14. **在线质量监控+反馈闭环** — eval 是离线 batch，缺实时飞轮
15. **多模态 Embedding** — 仅文本，缺图像/音频

**Phase F + G + H + I 全部完成！** 平台核心能力、Agent 高阶能力、安全合规三支柱齐备。接下来转向平台开发者体验（Phase J：SDK + Console V2 + 评测飞轮）。

---

## Phase F — 能力中台补全（7～8 月，⭐⭐⭐ 最高优先）

> **目标**：将能力中台从「有」升级为「可用」。

| Issue | 内容 | 依赖 | 工期 |
|-------|------|------|------|
| #29 ✅ | **Prompt 版本化**：版本表 + 模板渲染 + 灰度切换 API | — | 2w |
| #30 ✅ | **Prompt A/B 测试**：流量分桶 + 指标对比 + 自动胜出 | #29 ✅ | 1w |
| #31 ✅ | **长记忆持久化**：Postgres memory 表 + Redis 热缓存 + summarize API | — | 2w |
| #32 ✅ | **MCP 真实集成**：stdio/HTTP 双协议 + 工具注册·鉴权·版本管理 | — | 3w |
| #33 ✅ | **上下文压缩策略**：滑窗截断 + LLM 摘要压缩 + Token 感知注入 | #31 ✅ | 2w |

文档：[phase-f-prompt-registry.md](./phase-f-prompt-registry.md) · [phase-f-prompt-experiment.md](./phase-f-prompt-experiment.md) · [phase-f-memory.md](./phase-f-memory.md) · [phase-f-context-compress.md](./phase-f-context-compress.md) · [phase-f-mcp.md](./phase-f-mcp.md)

**Phase F 全部完成！** 能力中台从「有」升级为「可用」。

---

## Phase G — 模型服务增强（7～8 月，⭐⭐⭐ 可与 F 并行）

> **目标**：语义缓存是成本管控飞轮的关键，优先级极高。

| Issue | 内容 | 依赖 | 工期 |
|-------|------|------|------|
| #34 ✅ | **语义缓存**：Gateway 层 Embedding 相似度查询 + Redis 缓存 + 命中率 metrics | — | 2w |
| #35 | **Embedding 独立服务**：独立进程 + 独立 Key + SLA 监控 | — | 2w |
| #36 | **多模态 Embedding**：图文混合向量化支持 | #35 | 2w |

文档：[phase-g-semantic-cache.md](./phase-g-semantic-cache.md)

**并行分组**：`#34 + #35` 可同时开启，`#36` 依赖 `#35`

---

## Phase H — Agent 高阶能力（8～10 月，⭐⭐）

> **目标**：从「能跑」到「跑好」的核心跨越——控制流 + Multi-Agent。

| Issue | 内容 | 依赖 | 工期 |
|-------|------|------|------|
| #37 ✅ | **控制流编排引擎**：轻量 DAG + 条件分支 + 循环控制 + 子任务调度 | — | 3w |
| #38 ✅ | **Multi-Agent 协作框架**：主 Agent 委托子 Agent + 并行工具调用 + 结果聚合 | #37 ✅ | 4w |
| #39 ✅ | **Agent 生命周期管理**：版本注册 + 灰度发布 + 蓝绿切换 + 回滚 | — | 2w |
| #40 ✅ | **HITL 完整工作流**：审批队列（DB 存储）+ Webhook 通知 + 超时处理 | — | 2w |

文档：[phase-h-orchestrator.md](./phase-h-orchestrator.md) · [phase-h-multi-agent.md](./phase-h-multi-agent.md) · [phase-h-agent-lifecycle.md](./phase-h-agent-lifecycle.md) · [phase-h-hitl.md](./phase-h-hitl.md)

**Phase H 全部完成！** Agent 应用层从「能跑」升级为「跑好」。

---

## Phase I — 安全与合规深化（9～10 月，⭐⭐）

> **目标**：补全 AgentOps「管得住」支柱。

| Issue | 内容 | 依赖 | 工期 |
|-------|------|------|------|
| #41 ✅ | **沙箱容器隔离**：Docker seccomp profile + gVisor 可选 | — | 2w |
| #42 ✅ | **动作分级审计**：read-only / write / destructive 三级 + 审计日志 | — | 1w |
| #43 ✅ | **PII 脱敏 + 内容安全**：正则/模型双重检测 + 脱敏 API | — | 2w |
| #44 ✅ | **OAuth2 / mTLS**：生产级鉴权替换 JWT HS256（opt-in，默认关闭） | — | 3w |

文档：[phase-i-sandbox.md](./phase-i-sandbox.md) · [phase-i-audit-actions.md](./phase-i-audit-actions.md) · [phase-i-pii.md](./phase-i-pii.md) · [phase-i-auth.md](./phase-i-auth.md)

**Phase I 全部完成！** 安全合规能力齐备，进入生产可用阶段。

---

## Phase J — 平台开发者体验（10～12 月，⭐）

> **目标**：让外部开发者可以真正使用这个平台。
> **GitHub Issues**: [#29](https://github.com/xingyun0812/ai-platform-lab/issues/29) · [#30](https://github.com/xingyun0812/ai-platform-lab/issues/30) · [#31](https://github.com/xingyun0812/ai-platform-lab/issues/31) · [#32](https://github.com/xingyun0812/ai-platform-lab/issues/32)

| Issue | 内容 | 依赖 | 工期 | 状态 |
|-------|------|------|------|------|
| #45 (GH #29) ✅ | **Python SDK**：封装 Gateway/Agent/RAG API，参考 OpenAI SDK 风格 | — | 3w | 完成 |
| #46 (GH #30) ✅ | **Console V2**：真正的管理 UI（React），替换 HTML stub | — | 4w | 完成 |
| #47 (GH #31) ✅ | **评测数据集 + 离线 Pipeline**：基准数据集扩充 + CI 评测门禁 | — | 2w | 完成 |
| #48 (GH #32) ✅ | **在线质量监控 + 反馈飞轮**：实时 Bad Case 捕获 → Eval → Prompt 迭代 | #30 + #31 | 3w | 完成 |

文档：[phase-j-python-sdk.md](./phase-j-python-sdk.md) · [phase-j-console-v2.md](./phase-j-console-v2.md) · [phase-j-eval-pipeline.md](./phase-j-eval-pipeline.md) · [phase-j-feedback-loop.md](./phase-j-feedback-loop.md)

**Phase J 全部完成！** 开发者体验闭环：SDK → Console → 评测 → 反馈飞轮。

---

## Phase K — 生产基础设施（11 月+，⭐）

> **目标**：云原生化，支撑真实生产流量。
> **GitHub Issues**: [#33](https://github.com/xingyun0812/ai-platform-lab/issues/33) · [#34](https://github.com/xingyun0812/ai-platform-lab/issues/34) · [#35](https://github.com/xingyun0812/ai-platform-lab/issues/35) · [#36](https://github.com/xingyun0812/ai-platform-lab/issues/36)

| Issue | 内容 | 依赖 | 工期 | 状态 |
|-------|------|------|------|------|
| #49 (GH #33) ✅ | **对象存储接入**：S3/OSS 集成，替换本地文件存储 | — | 1w | 完成 |
| #50 (GH #34) ✅ | **K8s Helm Chart**：Gateway/Worker/Qdrant Chart + HPA | — | 4w | 完成 |
| #51 (GH #35) ✅ | **多 AZ 高可用**：跨 AZ 部署 + Qdrant 副本 + Redis Sentinel | #34 | 3w | 完成 |
| #52 (GH #36) ✅ | **GPU 弹性调度**：Embedding/Rerank 服务 GPU 节点 + 自动伸缩 | #34 | 3w | 完成 |

文档：[phase-k-object-storage.md](./phase-k-object-storage.md) · [phase-k-helm.md](./phase-k-helm.md) · [phase-k-multi-az.md](./phase-k-multi-az.md) · [phase-k-gpu-scheduling.md](./phase-k-gpu-scheduling.md)

**Phase K 全部完成！** 生产基础设施齐备：存储 → K8s → 多 AZ → GPU 调度。

---

## 已知限制（面试时主动说）

### 计费与用量

- 已支持 **按 token 落库**（Postgres）与日/月预算拦截；**未**区分 input/output 单价，无发票。
- 仍保留「按请求次数」日配额（`daily_request_quota`），与 token 预算并存。
- 配置 `REDIS_URL` 后配额与限流 **跨 gateway 实例共享**；未配置时仍回退进程内存。

### 可用性与扩展

- **单进程** Gateway；无水平扩展、无 leader 选举。
- Qdrant 单节点 Compose，无副本、无跨 AZ。
- Model Router fallback 为 **同步串行**尝试。

### 安全与合规

- 支持 **Env / Vault dev** 密钥引用；生产级 KMS / OAuth / mTLS 仍无。
- **无**细粒度 RBAC（按用户/角色/资源），仅租户级工具与模型 ACL。
- **无** PII 脱敏、内容安全策略、prompt 注入防护专项。

### Agent

- 工具集为内置 demo（calc、httpbin、kb snippet），**非**可插拔 MCP 市场。
- 无人工审批（human-in-the-loop）完整工作流、无长任务异步回调。
- Session 内存存储，重启丢失（Redis Session 已支持，但无跨 Session 长记忆）。
- **无** Multi-Agent 协作、无控制流编排引擎。

### 评测与 SRE

- CI 跑 lint + 冒烟 + baseline 校验；全量 RAG eval 门禁需 LLM Key。
- 无在线质量监控、无 Bad Case 反馈飞轮。
- 无 SLO/错误预算、无 on-call runbook。

---

## 如何讲「为什么先这样」

面试口述模板（约 10 分钟）：

1. **租户故事**：三假租户演示隔离 — 模型别名、工具 ACL、配额/限流分层。
2. **RAG 版本**：`kb_id + version` 可回放；低分拒答避免幻觉；hybrid+rerank 效果进阶。
3. **Agent 治理**：`allowed_tools` 在网关 enforce，轨迹可审计，HITL stub 可扩展。
4. **评测回归**：`baseline.jsonl` + `eval/run.py compare` 防退化，轨迹 eval 覆盖工具选择准确性。
5. **成本管控**：Token 计量 + 预算拦截 + 路由降级 + 语义缓存（规划中）形成飞轮。
6. **诚实边界**：引用本文「已知限制」，说明下一阶段 Multi-Agent / 控制流编排 / 语义缓存的演进。

---

## 非目标（本 repo 刻意不做）

- 训练/微调流水线
- 向量库自研
- 完整 LLMOps 产品 UI（Phase J Console V2 仅做管理面板）
- 真实支付与发票

---

## 文档导航

| 文档 | 说明 |
|------|------|
| [gap-analysis-diagram.md](./gap-analysis-diagram.md) | 现状 vs 目标架构 Mermaid 对比图 |
| [roadmap-gantt.md](./roadmap-gantt.md) | Phase F~K 甘特图 |
| [architecture.md](./architecture.md) | 平台整体架构叙事 |
| [phase-e-agent-quality.md](./phase-e-agent-quality.md) | Phase E Agent 效果深化 |
| [phase-d-future-evolution.md](./phase-d-future-evolution.md) | Phase D 远期规划 |
| [enterprise-ai-platform-sop.md](./enterprise-ai-platform-sop.md) | 大厂 SOP 踩坑对照 |
