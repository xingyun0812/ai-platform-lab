# 路线图与已知限制

本仓库目标是 **6 周学习闭环 + 面试可讲**，以下诚实列出「还没做」与「若上生产还需补什么」。

---

## 当前已完成（v0.1 学习版）

| 模块 | 状态 | 说明 |
|------|------|------|
| 多租户 Gateway | ✅ | 鉴权、日配额、trace_id |
| RAG 管道 | ✅ | 异步索引、kb 版本、Qdrant |
| RAG 问答 | ✅ | min_score 拒答、citations、baseline |
| Agent 运行时 | ✅ | 工具白名单、session、tool_trace |
| 观测与评测 | ✅ | OTel span、/metrics、eval/run.py |
| 硬化（第 6 周） | ✅ | Model Router、令牌桶、Compose 一键起 |
| 可内测（Phase A） | ✅ | Redis 共享配额/限流、Worker 队列、SQLite 审计、CI |

Phase A 文档：[phase-a-internal-beta.md](./phase-a-internal-beta.md)

---

## 已知限制（面试时主动说）

### 计费与用量

- 已支持 **按 token 落库**（Postgres）与日/月预算拦截；**未**区分 input/output 单价，无发票。
- 仍保留「按请求次数」日配额（`daily_request_quota`），与 token 预算并存。
- 配置 `REDIS_URL` 后配额与限流 **跨 gateway 实例共享**；未配置时仍回退进程内存。

### 可用性与扩展

- **单进程** Gateway；无水平扩展、无 leader 选举。
- Qdrant 单节点 Compose，无副本、无跨 AZ。
- `USE_INDEX_WORKER=true` 时索引走 Redis 队列 + 独立 worker；本地可关回 BackgroundTasks。
- Model Router fallback 为 **同步串行**尝试，无熔断器、无权重路由。

### 安全与合规

- 租户 Bearer 写在 yaml，**非** KMS / OAuth / mTLS。
- **无**细粒度 RBAC（按用户/角色/资源），仅租户级工具与模型 ACL。
- 审计为 **SQLite 文件**（`data/audit.db`），非集中式 SIEM；细粒度 RBAC 仍无。
- **无** PII 脱敏、内容安全策略、prompt 注入防护专项。

### 数据与 RAG

- 知识库版本为整数递增，**无**灰度发布与自动回滚。
- 检索仅向量 top_k，**无**混合检索、重排序、多路召回。
- Embedding 与 LLM 共用同一上游 Key，**无**独立 embedding 服务治理。

### Agent

- 工具集为内置 demo（calc、httpbin、kb snippet），**非**可插拔 MCP 市场。
- 无人工审批（human-in-the-loop）、无长任务异步回调。
- Session 内存存储，重启丢失。

### 评测与 SRE

- CI 跑 lint + 冒烟 + baseline 校验；**全量** RAG eval 门禁需 LLM Key（`--min-pass-rate`）。
- `/metrics` 为进程内聚合，**未**接 Prometheus/Grafana 样板。
- 无 SLO/错误预算、无 on-call runbook。

---

## 建议演进路线（按性价比）

### Phase A — 可内测 ✅ 已完成

1. ✅ Redis 共享配额 + 令牌桶（`REDIS_URL`，可回退内存）
2. ✅ SQLite 审计表 + `GET /internal/audit/recent`
3. ✅ GitHub Actions CI + `eval/run.py validate-baseline` / `--min-pass-rate`
4. ✅ 独立 Worker + Redis 索引队列（`USE_INDEX_WORKER`）

详见 [phase-a-internal-beta.md](./phase-a-internal-beta.md)。

### Phase B — 可小流量生产（1～2 月）

| 波次 | Issue | 内容 |
|------|-------|------|
| B1 ✅ | [#5](https://github.com/xingyun0812/ai-platform-lab/issues/5) | Postgres + token 用量落库 |
| B1 ✅ | [#6](https://github.com/xingyun0812/ai-platform-lab/issues/6) | 租户 token 预算、拦截、用量 API（依赖 #5） |
| B2 并行 | [#7](https://github.com/xingyun0812/ai-platform-lab/issues/7) | 密钥托管抽象（Env + Vault dev profile） |
| B2 并行 | [#8](https://github.com/xingyun0812/ai-platform-lab/issues/8) | RAG 混合检索（向量 + 关键词融合） |
| B2 并行 | [#10](https://github.com/xingyun0812/ai-platform-lab/issues/10) | OTel Collector + Jaeger + Prometheus |
| B3 | [#9](https://github.com/xingyun0812/ai-platform-lab/issues/9) | RAG rerank + kb 版本金丝雀（依赖 #8） |

B1 文档：[phase-b-small-production.md](./phase-b-small-production.md)

路线图四项与 issue 映射：

1. 按 token 计量 + 租户预算 → **#5 + #6**
2. 密钥托管 → **#7**
3. RAG 混合检索 + rerank；kb 金丝雀 → **#8 + #9**
4. OTel Collector → Jaeger/Tempo + Prometheus → **#10**

### Phase C — 平台化

1. 多 region 路由与数据驻留
2. 租户自助控制台（模型、工具、配额、知识库）
3. Agent 工具市场 + 审批流
4. 多模型供应商抽象（价格/延迟/能力矩阵）

---

## 非目标（本 repo 刻意不做）

- 训练/微调流水线
- 向量库自研
- 完整 LLMOps 产品 UI
- 真实支付与发票

---

## 如何讲「为什么先这样」

面试口述模板（约 10 分钟）：

1. **租户故事**：三假租户演示隔离 — 模型别名、工具 ACL、配额/限流分层。
2. **RAG 版本**：`kb_id + version` 可回放；低分拒答避免幻觉。
3. **Agent 治理**：`allowed_tools` 在网关 enforce，轨迹可审计。
4. **评测回归**：`baseline.jsonl` + `eval/run.py compare` 防退化。
5. **诚实边界**：引用本文「已知限制」，说明下一阶段的 Redis/计费/Worker。

详见 [architecture.md](./architecture.md) 与 [week6-hardening.md](./week6-hardening.md)。
