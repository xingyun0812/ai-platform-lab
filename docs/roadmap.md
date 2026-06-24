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

## Phase L — 工程深度与面试叙事（✅ 已完成）

> **目标**：不扩新模块，把 stub / 未验证能力做深、做真、串成故事。  
> **规划**：[phase-l-engineering-depth.md](./phase-l-engineering-depth.md) · Issue 正文：[issues-backlog-phase-l.md](./issues-backlog-phase-l.md)  
> **Tag**：`phase-l-engineering-depth`（2026-06-23）

| Issue | 波次 | 内容 | 依赖 | 工期 | 状态 |
|-------|------|------|------|------|------|
| #53 | L0 | 文档状态对齐（roadmap / gap / 远期规划） | — | 2d | ✅ |
| #54 | L1 | RAG 真 Rerank Provider | #53 | 1.5w | ✅ |
| #55 | L1 | RAG 增量索引 | — | 1w | ✅ |
| #56 | L1 | Eval LLM-as-Judge | #54 | 1.5w | ✅ |
| #57 | L1 | kb 金丝雀自动回滚 Job | #56 | 1w | ✅ |
| #58 | L2 | Agent 三率指标 | — | 1w | ✅ |
| #59 | L2 | Agent Vertical（Orchestrator + HITL） | #58 | 1.5w | ✅ |
| #60 | L2 | Agent Baseline 扩充 + CI 门禁 | #58 | 1w | ✅ |
| #61 | L3 | 反馈飞轮 E2E 实测 | #56, #60 | 1w | ✅ |
| #62-console | L3 | Console 集成跑真（build/挂载/API） | — | 3～5d | ✅ |
| #62 | L3 | 平台 Demo 脚本 + 面试叙事手册 | #62-console | 1w | ✅ |
| #63 | L3 | SDK 端到端 smoke | #62-console | 1～2d | ✅ |

> ROI 说明：[phase-l-priority-roi.md](./phase-l-priority-roi.md) · Console 交付：[phase-l-console-integration.md](./phase-l-console-integration.md) · Demo：[demo-walkthrough.md](./demo-walkthrough.md)

**Phase L 全部完成！** 工程深度 + Demo + 面试叙事已交付；合并 PR #48～#59、#61。

---

## Phase M — RAG 增量索引做满（✅ 已完成）

> **目标**：向量增量之上，补齐 BM25 差量、purge-source、指标与 demo 断言。  
> **规划**：[phase-m-incremental-index.md](./phase-m-incremental-index.md) · Issue 正文：[issues-backlog-phase-m.md](./issues-backlog-phase-m.md)  
> **Tag**：`phase-m-incremental-index`（2026-06-23）

| Issue | 内容 | 状态 |
|-------|------|------|
| #63 | BM25 按 source 增量 merge | ✅ |
| #64 | purge-source 清理向量+BM25 | ✅ |
| #65 | 任务 API + Prometheus 指标 | ✅ |
| #66 | platform_demo 二次索引断言 | ✅ |

**Phase M 全部完成！** 堆叠 PR #68～#71。

---

## Phase N — Python SDK 发布 PyPI（✅ 已完成）

> **规划**：[phase-n-pypi-sdk.md](./phase-n-pypi-sdk.md) · Issue 正文：[issues-backlog-phase-n.md](./issues-backlog-phase-n.md)  
> **Tag**：`phase-n-pypi-sdk`（2026-06-24）

| Issue | 内容 | PR | 状态 |
|-------|------|-----|------|
| #76 | 规划文档 | #81 | ✅ |
| #77 | SDK 包元数据 + README | #82 | ✅ |
| #78 | `publish-sdk.yml` PyPI 发布 | #83 | ✅ |
| #79 | `eval/sdk_pypi_smoke.sh` | #84 | ✅ |
| #80 | 文档 / roadmap / 叙事同步 | #85 | ✅ |

**诚实边界**：PyPI 包为 HTTP 客户端；首次生产发版需维护者配置 Trusted Publishing 或 `PYPI_API_TOKEN` 后打 `sdk-v*` tag。

---

## Phase O — Agent 能力对齐 JD2（✅ 已完成）

> **动机**：智能体研发岗 JD §4.1 缺口补齐（Planner / CoT / Multi-Agent v2 / 外部工具 / 数据分析 vertical）  
> **规划**：[phase-o-agent-jd2-alignment.md](./phase-o-agent-jd2-alignment.md) · Issue 正文：[issues-backlog-phase-o.md](./issues-backlog-phase-o.md) · 规划 Issue [#86](https://github.com/xingyun0812/ai-platform-lab/issues/86)  
> **Tag**：`phase-o-agent-jd2` · **门禁**：`python eval/agent_jd2_gate.py run`

| Issue | 内容 | 状态 |
|-------|------|------|
| #86 | 规划文档 | ✅ |
| #87 | Task Planner + 任务分解 | ✅ #99 |
| #88 | CoT 推理模式 | ✅ #100 |
| #89 | Multi-Agent v2 黑板 | ✅ #101 |
| #90 | Plugin Manifest | ✅ #102 |
| #91 | web_search 工具 | ✅ #103 |
| #92 | sql_query 只读 | ✅ #104 |
| #93 | 数据分析 Vertical | ✅ #105 |
| #94 | Agent 性能 | ✅ #106 |
| #95 | 文档 Demo gate | ✅ #107 |

**非目标**：RPA、PyTorch、LangChain 依赖绑定。

---

## Phase P — 多模态 Embedding ✅

> **动机**：Phase G Embedding 仅文本；甘特图 Phase G3「多模态 Embedding」落地  
> **规划**：[phase-p-multimodal-embedding.md](./phase-p-multimodal-embedding.md) · [issues-backlog-phase-p.md](./issues-backlog-phase-p.md)  
> **Tag**：`phase-p-multimodal` · **门禁**：`python eval/multimodal_embedding_gate.py run`

| Issue | 内容 | 状态 |
|-------|------|------|
| P1 | 多模态 inputs + stub API | ✅ #108 |
| P2 | RAG 图文索引 | ✅ #110 |
| P3 | Console / SDK | ✅ #111 |
| P4 | eval 门禁 + tag | ✅ #113 |

---

## Phase Q — 任务规划前沿对齐（📋 规划中）

> **动机**：Phase O O1 为 JD 对齐 MVP（一次 LLM Plan + 串行执行）；对齐 Plan-and-Execute / LangGraph 思想需并行 DAG、重规划、Plan HITL、质量门禁  
> **规划**：[phase-q-advanced-planning.md](./phase-q-advanced-planning.md) · [issues-backlog-phase-q.md](./issues-backlog-phase-q.md)  
> **Tag**（计划）：`phase-q-advanced-planning` · **门禁**（计划）：`python eval/plan_quality_gate.py run`  
> **前置**：Phase O O1 ✅（#87）

| Issue | 内容 | 状态 |
|-------|------|------|
| [#115](https://github.com/xingyun0812/ai-platform-lab/issues/115) | 规划文档 + milestone | 📋 |
| [#116](https://github.com/xingyun0812/ai-platform-lab/issues/116) | Structured Plan 输出 | 📋 |
| [#117](https://github.com/xingyun0812/ai-platform-lab/issues/117) | DAG 并行 step 执行 | 📋 |
| [#118](https://github.com/xingyun0812/ai-platform-lab/issues/118) | 失败重规划 Critic | 📋 |
| [#119](https://github.com/xingyun0812/ai-platform-lab/issues/119) | Plan 级 HITL | 📋 |
| [#120](https://github.com/xingyun0812/ai-platform-lab/issues/120) | Planner ↔ Orchestrator 桥接 | 📋 |
| [#121](https://github.com/xingyun0812/ai-platform-lab/issues/121) | 规划质量 eval + tag | 📋 |

**非目标**：LangGraph 依赖、Tree-of-Thoughts、替换现有 ReAct Runtime。

**是否开搞**：冲 JD2 演示可暂缓；被追问与 LangGraph 差距或要做技术深度时，优先 **Q1 + Q2 + Q6**。

---

## 已知限制（面试时主动说）

> **说明**：Phase A～K 已交付大量能力（MCP、HITL、Multi-Agent、语义缓存、PII、Console V2 等），本节区分 **「已有但 opt-in / 实验级」** 与 **「仍缺或仍为 stub」**，避免与 README 矛盾。Phase L 目标是把 stub 做深，见 [phase-l-engineering-depth.md](./phase-l-engineering-depth.md)。

### 计费与用量

- 已支持 **按 token 落库**（Postgres）与日/月预算拦截；**未**区分 input/output 单价，无发票。
- 仍保留「按请求次数」日配额（`daily_request_quota`），与 token 预算并存。
- 配置 `REDIS_URL` 后配额与限流 **跨 gateway 实例共享**；未配置时仍回退进程内存。

### 可用性与扩展

- **单进程** Gateway 为默认开发形态；Helm Chart 支持 K8s 部署，但 **未**在生产环境压测水平扩展与 leader 选举。
- Qdrant 默认单节点 Compose；多 AZ 模板（`values-multi-az.yaml`）**配置级**，未实际跨 AZ 演练。
- Model Router fallback 为 **同步串行**尝试。

### 安全与合规

- **OAuth2 / mTLS 已实现**（`OAUTH2_ENABLED` / `MTLS_ENABLED`），默认关闭；生产级 KMS/HSM、集中 SIEM **无**。
- **RBAC 为租户级**：工具/模型 ACL + JWT；**无**细粒度用户/角色/资源策略。
- **PII 脱敏已实现**（`packages/pii/`，REST + 策略 CRUD），非完整 DLP 产品；Guardrails 为规则 + stub 扩展点。
- **沙箱已实现**（`SANDBOX_ENABLED`），默认关闭；无 gVisor/Firecracker 级隔离。

### RAG（Phase L #54～#57 ✅）

- Hybrid 检索 + 金丝雀路由 + **真 Rerank API**（`RAG_RERANK_MODE=api`）。
- 向量层 chunk 指纹增量（#55）+ **BM25 按 source 差量、purge-source、Prometheus `rag_index_*`**（Phase M #63～#66）。
- Eval 支持 **LLM-as-Judge**（#56）；金丝雀 **自动回滚 Job**（#57）。

### Agent（Phase L #58～#60 ✅）

- **内置工具 + MCP 桥接已有**（`config/mcp_tools.json` / `/internal/mcp/servers`），非公开市场/动态注册生态。
- **HITL 完整工作流已有**（审批 REST + destructive 强制）；长任务异步回调 **无**。
- **Redis Session 已支持**（`REDIS_URL`）；**Memory Store** 需 `MEMORY_STORE_ENABLED`；跨租户长记忆治理仍浅。
- **Multi-Agent + Orchestrator + Vertical 演示链**（#59）；Agent 四率 + CI gate（#58/#60）。

### 模型服务

- **语义缓存已实现**（exact/embedding 模式，`SEMANTIC_CACHE_ENABLED`），默认关闭；命中率调优与生产验证 **有限**。
- Embedding 随 Gateway 调用；**独立 Embedding 治理面**（配额/多租户计量）仍浅。

### 评测与 SRE

- CI：lint + 冒烟 + RAG/Agent baseline gate；全量 live eval **需 LLM Key**。
- **反馈飞轮 live 闭环已验收**（#61 `feedback_loop_demo --live`）。
- Prometheus/Grafana 面板 **已有**；SLO/错误预算/on-call runbook **无**。

### 开发者体验

- **Console V2 已挂载** → `/console/`（见 [phase-l-console-integration.md](./phase-l-console-integration.md)）。
- **Python SDK**：`pip install ai-platform-lab`（Phase N，`publish-sdk.yml` + `eval/sdk_pypi_smoke.sh`）；本地开发仍可用 `pip install -e sdk/python`；TS SDK **无**。
- Demo：`./eval/platform_demo.sh`、`eval/sdk_smoke.py`、`./eval/sdk_pypi_smoke.sh --local`。

---

## 如何讲「为什么先这样」

面试口述模板（约 10 分钟）：

1. **租户故事**：三假租户演示隔离 — 模型别名、工具 ACL、配额/限流分层。
2. **RAG 版本**：`kb_id + version` 可回放；低分拒答避免幻觉；hybrid+rerank 效果进阶。
3. **Agent 治理**：`allowed_tools` 在网关 enforce，轨迹可审计，HITL stub 可扩展。
4. **评测回归**：`baseline.jsonl` + `eval/run.py compare` 防退化，轨迹 eval 覆盖工具选择准确性。
5. **成本管控**：Token 计量 + 预算拦截 + 路由降级 + 语义缓存（opt-in）形成飞轮。
6. **诚实边界**：引用本文「已知限制」— RBAC 仍浅；Phase L/M 已补齐 Rerank/Judge/回滚/Agent 三率/飞轮/增量索引 live。

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
| [phase-l-engineering-depth.md](./phase-l-engineering-depth.md) | Phase L 工程深度与面试叙事 ✅ |
| [interview-narrative.md](./interview-narrative.md) | 10 分钟面试口述稿 + Q&A |
| [phase-l-priority-roi.md](./phase-l-priority-roi.md) | Phase L ROI 优先级与 Issue 对照 |
| [phase-l-console-integration.md](./phase-l-console-integration.md) | Phase L Console 集成 ✅ |
| [demo-walkthrough.md](./demo-walkthrough.md) | 15 分钟平台 Demo 脚本 |
| [issues-backlog-phase-l.md](./issues-backlog-phase-l.md) | Phase L Issue 正文 #53～#63 |
