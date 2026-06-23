# ai-platform-lab

最小 **AI 中台** 实验仓库（与 [《AI中台学习执行手册》](docs/AI中台学习执行手册.md) 配套）。当前完成 **Phase A～M**：模型网关、RAG（含增量索引）、Agent、观测评测、硬化、Prompt 版本化、长记忆、MCP、上下文压缩、语义缓存、Embedding 服务、控制流编排、Multi-Agent、HITL、沙箱、分级审计、PII 脱敏、OAuth2/mTLS、Python SDK、Console V2、评测 Pipeline、反馈飞轮、对象存储、K8s Helm、多 AZ、GPU 调度。

> 📊 **当前状态**：Phase A～M 完成 · tag `phase-m-incremental-index` · PR #68～#71 已合并
> 📋 **完整状态报告**：[docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)

## 15 分钟快速跑通

```bash
cd /Users/zhangyue/IdeaProjects/ai-platform-lab
cp .env.example .env          # 填 LLM_BASE_URL + LLM_API_KEY，见 docs/local-llm-setup.md
docker compose up -d --build  # postgres + redis + gateway :8000 + worker + qdrant
curl -s http://127.0.0.1:8000/healthz
```

有 API Key 时继续：

```bash
# 索引样例文档 → 轮询 /internal/tasks/{id} 至 succeeded
curl -s http://127.0.0.1:8000/internal/index \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"kb_id":"lab-demo","version":1,"source_uri":"samples/hello.txt"}'

curl -s http://127.0.0.1:8000/v1/rag/query \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"tenant_id":"admin","kb_id":"lab-demo","version":1,"query":"RAG 数据管道是什么"}'

python eval/run.py run
```

平台叙事：[docs/architecture.md](docs/architecture.md) · 已知限制：[docs/roadmap.md](docs/roadmap.md) · Phase A：[docs/phase-a-internal-beta.md](docs/phase-a-internal-beta.md)

---

## 协作贡献

欢迎贡献！请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 了解 **Issue 驱动开发流程**。

- **认领 Issue** → [Issues 待办](https://github.com/xingyun0812/ai-platform-lab/issues)
- **提 PR 前必读** → [CONTRIBUTING.md](CONTRIBUTING.md)（分支命名 / commit 规范 / 测试要求）
- **待创建 Issue 清单** → [docs/issues-backlog.md](docs/issues-backlog.md)（#45-#52）
- **讨论/提问** → [Discussions](https://github.com/xingyun0812/ai-platform-lab/discussions)

---

## 环境（本地开发）

- Python **3.11+**
- Docker（可选，推荐 Compose 一键起）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip && pip install -e .
uvicorn apps.gateway.main:app --reload --host 127.0.0.1 --port 8000
```

## 租户与鉴权

配置见 `config/tenants.yaml`。请求 **必须** 同时携带：

- `X-Tenant-Id`：与 yaml 中键一致（如 `demo-a`）
- `Authorization: Bearer <与 yaml 中 bearer_token 一致>`

第 6 周起支持：`default_model`（别名）、`rate_limit_rps/burst`（令牌桶）。

## 调用示例（非流式）

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: demo-a" \
  -H "Authorization: Bearer sk-tenant-demo-a-change-me" \
  -d '{
    "model": "chat-fast",
    "messages": [{"role": "user", "content": "用一句话介绍你自己"}]
  }'
```

`chat-fast` 映射见 `config/models.yaml`。未配置 `LLM_API_KEY` 时返回 **503** `UPSTREAM_NOT_CONFIGURED`。

## RAG 管道（第 2 周）

详见 [docs/week2-rag-pipeline.md](docs/week2-rag-pipeline.md)。Compose 已含 Qdrant，无需单独 `--profile vectors`。

## RAG 问答（第 3 周）

详见 [docs/week3-rag-query.md](docs/week3-rag-query.md)。评测用例 [eval/baseline.jsonl](eval/baseline.jsonl)。

## Agent 运行时（第 4 周）

详见 [docs/week4-agent-runtime.md](docs/week4-agent-runtime.md)。

## 观测与评测（第 5 周）

```bash
curl -s http://127.0.0.1:8000/metrics
python eval/run.py run
python eval/run.py compare eval/runs/run_a.json eval/runs/run_b.json
python eval/load_smoke.py --concurrency 50
```

详见 [docs/week5-observability-eval.md](docs/week5-observability-eval.md)。

## 硬化与平台叙事（第 6 周）

- **Model Router**：`config/models.yaml` 别名 + 失败降级
- **限流**：租户令牌桶 → `RATE_LIMIT_EXCEEDED`
- **Compose**：`docker compose up -d --build`

详见 [docs/week6-hardening.md](docs/week6-hardening.md)。

## Phase A — 可内测

- **Redis**：`REDIS_URL` 共享日配额与令牌桶（未配置则回退内存）
- **Worker**：`USE_INDEX_WORKER=true` 时索引入 Redis 队列，worker BLPOP 执行
- **审计**：`GET /internal/audit/recent`（SQLite `data/audit.db`）
- **CI**：`.github/workflows/ci.yml`（ruff + 冒烟 + baseline 校验）

```bash
python eval/run.py validate-baseline
python eval/agent_run.py validate-baseline
# Agent 轨迹评测（需 Key）：python eval/agent_run.py run
python eval/acceptance_smoke.py
```

详见 [docs/phase-a-internal-beta.md](docs/phase-a-internal-beta.md)。

## Phase B1 — Token 计量与预算

- **Postgres**：`DATABASE_URL` + `usage_records` 表（chat/RAG/agent 上游 `usage`）
- **预算**：`token_budget_daily/monthly`（`demo-b` 默认日预算 500 tokens）
- **API**：`GET /internal/billing/usage`、`GET /internal/billing/export`（admin）
- 超限 → `429 BUDGET_EXCEEDED`（与 `QUOTA_EXCEEDED` 区分）

```bash
curl -s "http://127.0.0.1:8000/internal/billing/usage?hours=24" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me"
```

详见 [docs/phase-b-small-production.md](docs/phase-b-small-production.md)。

## Phase B2 — 密钥 / 混合检索 / 可观测（并行）

- **密钥**：`SECRETS_PROVIDER=env|vault`，`bearer_secret_ref` 或 `LLM_SECRET_REF`
- **RAG hybrid**：`RAG_RETRIEVAL_MODE=hybrid`（BM25 + 向量 RRF，需重新索引）
- **可观测 profile**：`docker compose --profile observability up -d` → Jaeger `:16686`、Prometheus `:9090`

```bash
docker compose --profile vault up -d          # 可选 Vault :8200
docker compose --profile observability up -d  # 可选可观测栈
```

详见 [docs/phase-b2-parallel.md](docs/phase-b2-parallel.md)。

## Phase B3 — rerank + kb 金丝雀

- **Rerank**：`RAG_RERANK_ENABLED=true`（stub 词面重排，检索链 retrieve → rerank → LLM）
- **金丝雀**：`config/rag.yaml` → `kb_routing.{kb_id}.canary_percent`；回滚设为 `0`
- **路由查询**：`GET /internal/kb/{kb_id}/routing`

```bash
python eval/canary_stats.py --samples 1000   # 命中率模拟
```

详见 [docs/phase-b3-rerank-canary.md](docs/phase-b3-rerank-canary.md)。

## Phase C — 平台化

- **供应商矩阵**：`GET /internal/providers/matrix`，`routing_policy` 选型
- **Region**：`X-Region` + `data_zone` 驻留，`GET /internal/regions`
- **租户 API**：`GET /internal/tenants/{id}/profile`，`PATCH .../limits`
- **工具市场**：申请 → admin 审批 → `data/tenant_overrides.json`

详见 [docs/phase-c-platform.md](docs/phase-c-platform.md)。

## Phase D — 运维 / 治理 / 控制台

- **运维**：熔断器、Grafana `:3000`、`docker compose --scale gateway=2`
- **治理**：JWT + RBAC、审计 Postgres 双写
- **控制台**：http://127.0.0.1:8000/console/
- **账单**：`GET /internal/billing/invoice?month=YYYY-MM`

详见 [docs/phase-d-ops.md](docs/phase-d-ops.md)。远期见 [phase-d-future-evolution.md](docs/phase-d-future-evolution.md)。

## Phase E — Agent 效果深化

- **轨迹评测**：`eval/agent_run.py` + `agent_baseline.jsonl`（`expect_tools`、Tool Precision@1）
- **选工具准**：`tool_router.py` 意图路由 / Tool-RAG Top-K
- **长上下文**：`context_budget.py` 滚动摘要 + Token 预算
- **质量门**：`quality_gate.py` + 工具 envelope；低质量反思 hint
- **HITL / Shadow**：高风险工具 `202 pending_approval`；`X-Agent-Shadow: true`

```bash
python eval/agent_run.py validate-baseline
python eval/agent_run.py run --min-pass-rate 0.8   # 需 LLM_API_KEY
```

详见 [docs/phase-e-agent-quality.md](docs/phase-e-agent-quality.md)。大厂 SOP 对照见 [enterprise-ai-platform-sop.md](docs/enterprise-ai-platform-sop.md)。

## Phase G — 语义缓存（#34）

- **双模式命中**：`exact`（SHA256 精确）/ `semantic`（embedding 余弦相似度）
- **存储**：进程内 LRU+TTL / Redis 跨实例（`REDIS_URL` 可达时自动切换）
- **跳过**：`stream=true` / `temperature > 0.3` / 模型黑名单
- **指标**：`/metrics` 暴露 `semantic_cache_hits_total`、`tokens_saved_total`、`lookup_latency_ms_p95`

```bash
# 启用
echo "SEMANTIC_CACHE_ENABLED=true" >> .env
echo "SEMANTIC_CACHE_MODE=exact" >> .env   # 无 LLM_API_KEY 也能用

# 验证
python3 tests/test_semantic_cache.py
curl -s http://127.0.0.1:8000/metrics | grep semantic_cache
```

详见 [docs/phase-g-semantic-cache.md](docs/phase-g-semantic-cache.md)。Gap 分析与 Roadmap 见 [docs/gap-analysis-diagram.md](docs/gap-analysis-diagram.md) 与 [docs/roadmap-gantt.md](docs/roadmap-gantt.md)。

## Phase F — Prompt 版本化（#29）

- **版本化资产**：`draft` → `active` → `archived` 状态机，同 prompt_id 仅一个 active
- **双存储**：`config/prompts.yaml`（git）+ `data/prompt_overrides.json`（运行时）
- **模板语法**：`{{var}}` 双花括号（不与现有 `{context}` 冲突）
- **REST API**：`GET/POST /internal/prompts/*` 创建版本、切换 active、渲染预览
- **向后兼容**：prompt_id 不在 registry 时自动回退 legacy txt

详见 [docs/phase-f-prompt-registry.md](docs/phase-f-prompt-registry.md)。

## Phase F — Prompt A/B 实验（#30）

- **流量分桶**：`hash(experiment_id + bucket_key) → 0-99` 确定性分桶，同用户同问题稳定
- **指标体系**：requests / latency_p95 / tokens / errors / quality_scores
- **自动胜出**：达到 `min_samples` 且 `winner_margin` 满足时自动停止 + 标记 winner
- **手动 promote**：admin 显式提升 winner 为 active（不自动切换，防误判）
- **REST API**：`POST /internal/prompts/{id}/experiments/*`

详见 [docs/phase-f-prompt-experiment.md](docs/phase-f-prompt-experiment.md)。

## Phase F — 长记忆持久化（#31）

- **三级 Scope**：`session` 短期 / `user` 中期 / `tenant` 共享
- **存储**：Postgres 持久化主存 / 进程内兜底（`DATABASE_URL` 可达时自动切换）
- **自动摘要**：Agent 每 N 轮自动调用 LLM 压缩历史并持久化
- **检索**：keyword 模糊匹配（默认）/ semantic embedding（可选）
- **REST API**：`POST/GET/DELETE /internal/memory/*`

详见 [docs/phase-f-memory.md](docs/phase-f-memory.md)。

## Phase F — 上下文压缩（#33）

- **三层压缩**：L1 滑窗截断 → L2 LLM 摘要 → L3 Token 感知注入
- **LLM 摘要**：替换 `stub_summarize`，失败自动降级 stub
- **Token 感知注入**：检索 session 长记忆，按剩余 budget 动态注入 system prompt
- **降级链**：LLM 失败 → stub → 完全关闭

详见 [docs/phase-f-context-compress.md](docs/phase-f-context-compress.md)。

## Phase F — MCP 真实集成（#32）

- **双协议**：`stdio`（本地子进程）/ `http`（远程 server）
- **JSON-RPC 2.0**：`initialize` + `tools/list` + `tools/call`
- **工具桥接**：MCP 工具自动转 `ToolDefinition`，命名 `mcp_{server_id}_{tool}`
- **失败降级**：单 server 失败不影响其他；全部失败回退 `mcp_stub.py`
- **REST API**：`POST/GET/DELETE /internal/mcp/servers/*`

详见 [docs/phase-f-mcp.md](docs/phase-f-mcp.md)。

## Phase H — 控制流编排引擎（#37）

- **DAG 工作流**：节点 + 边 + 条件跳转
- **7 种节点类型**：`start` / `end` / `llm_call` / `tool_call` / `condition` / `parallel` / `loop` / `output`
- **模板渲染**：`${node_id.field}` 引用前序节点输出
- **沙箱条件求值**：支持比较 / 布尔运算 / 字符串匹配
- **安全限制**：max_steps + timeout + max_parallel 防死循环
- **REST API**：`POST/GET/DELETE /internal/orchestrator/workflows/*`

详见 [docs/phase-h-orchestrator.md](docs/phase-h-orchestrator.md)。

## Phase H — Multi-Agent 协作框架（#38）

- **4 种 Agent 角色**：`primary` / `specialist` / `reviewer` / `router`
- **委托模式**：主 Agent 委托子 Agent + 并行委托 + 结果聚合
- **防递归**：委托栈 + 最大深度 + 双向可委托标志
- **工具白名单**：AgentSpec 限制子 Agent 可用工具
- **编排集成**：`agent_call` 节点让委托成为工作流一等公民
- **REST API**：`POST/GET/DELETE /internal/agents/*` + `POST /delegate`

```bash
# 注册 RAG 专家 Agent
curl -s -X POST http://127.0.0.1:8000/internal/agents \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"rag_specialist","name":"RAG 专家","role":"specialist","system_prompt":"你是 RAG 专家","allowed_tools":["get_kb_snippet"]}'

# 委托任务
curl -s -X POST http://127.0.0.1:8000/internal/agents/rag_specialist/delegate \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" -d '{"task":"检索 RAG 资料"}'
```

详见 [docs/phase-h-multi-agent.md](docs/phase-h-multi-agent.md)。

## Phase H — Agent 生命周期管理（#39）

- **版本注册**：AgentSpec 快照 + 自动版本号
- **激活策略**：`all_at_once` / `canary` / `blue_green`
- **一键回滚**：revert 到 `previous_version`
- **流量分割**：canary 模式按百分比分流
- **REST API**：`POST /internal/agent-lifecycle/{agent_id}/versions` + `/activate` + `/rollback`

详见 [docs/phase-h-agent-lifecycle.md](docs/phase-h-agent-lifecycle.md)。

## Phase H — HITL 完整工作流（#40）

- **审批队列**：DB 存储（SQLite 可选）/ 内存兜底
- **Webhook 通知**：HMAC-SHA256 签名 + 指数退避重试
- **超时处理**：自动 expire 过期请求
- **完整状态机**：`pending` → `approved`/`rejected`/`timeout`/`cancelled`
- **REST API**：`POST /internal/hitl/approvals` + `/approve` + `/reject` + `/cancel`
- **向后兼容**：`packages/agent/hitl.py` 委托到新 service

详见 [docs/phase-h-hitl.md](docs/phase-h-hitl.md)。

## Phase G — Embedding 独立服务（#35）

- **Provider 抽象**：`StubProvider`（测试）/ `OpenAIProvider`（生产）
- **模型注册表**：YAML + JSON overrides
- **LRU 缓存**：text hash → embedding，避免重复计算
- **REST API**：`POST /internal/embeddings/embed` + 模型 CRUD + 缓存管理
- **独立部署**：可从 RAG 管道解耦，独立扩缩容

详见 [docs/phase-g-embedding.md](docs/phase-g-embedding.md)。

## Phase I — 沙箱容器隔离（#41）

- **3 种运行时**：`process`（回退）/ `docker`（seccomp）/ `gvisor`（最强隔离）
- **seccomp 档案**：strict / default / networking / readonly
- **资源限制**：memory + cpu + timeout
- **REST API**：`POST /internal/sandbox/execute` + 档案 CRUD
- **默认关闭**：`SANDBOX_ENABLED=false`

详见 [docs/phase-i-sandbox.md](docs/phase-i-sandbox.md)。

## Phase I — 动作分级审计（#42）

- **4 级分类**：`read_only` / `write` / `destructive` / `network`
- **启发式分类**：工具名含 delete/rm → destructive；create/update → write；get/search → read_only
- **destructive 强制审批**：与 #40 HITL 集成
- **审计日志**：每次工具调用记录 action_level + 状态
- **REST API**：`POST /internal/audit-actions/classify` + 审计动作查询

详见 [docs/phase-i-audit-actions.md](docs/phase-i-audit-actions.md)。

## Phase I — PII 脱敏 + 内容安全（#43）

- **PII 检测**：email / phone / SSN / credit card / IPv4 / 中国身份证 / 中国手机号
- **4 种脱敏动作**：`redact`（替换占位）/ `mask`（保留首尾）/ `hash`（SHA256）/ `block`（阻断）
- **内容安全**：关键词检测（hate / violence / sexual / self_harm）
- **完整管道**：detect + redact + safety 一站式
- **REST API**：`POST /internal/pii/process` + 模式/策略 CRUD

详见 [docs/phase-i-pii.md](docs/phase-i-pii.md)。

## Phase I — OAuth2 / mTLS（#44）

- **OAuth2 流程**：authorization code + client credentials + refresh token
- **mTLS 双向证书校验**：server + client 证书 + CA
- **JWT 回退**：`OAUTH2_JWT_FALLBACK=true` 时 OAuth2 失败回退 JWT
- **opt-in 设计**：默认关闭，保持现有 JWT HS256 鉴权
- **REST API**：`GET /internal/auth/oauth2/authorize` + callback + userinfo

详见 [docs/phase-i-auth.md](docs/phase-i-auth.md)。

## Phase J — Python SDK（#29）

- **OpenAI SDK 风格**：`client.chat.completions.create(...)`
- **6 个资源类**：chat / rag / agent / embedding / memory / orchestrator
- **同步 + 异步**：`Client` + `AsyncClient`
- **类型注解完整**：py.typed marker
- **独立包**：`pip install -e sdk/python`

```python
from ai_platform_lab import Client
c = Client("http://localhost:8000", api_key="sk-...")
r = c.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":"hi"}])
```

详见 [docs/phase-j-python-sdk.md](docs/phase-j-python-sdk.md)。

## Phase J — Console V2（#30）

- **React + Vite + TypeScript**：替换 HTML stub
- **8 个页面**：Dashboard / Tenants / Agents / RAG / Memory / Orchestrator / Audit / Settings
- **Ant Design 5**：企业级 UI 组件
- **JWT 登录**：token 存 localStorage
- **构建产物**：`apps/console/static/`，由 FastAPI 托管

详见 [docs/phase-j-console-v2.md](docs/phase-j-console-v2.md)。

## Phase J — 评测 Pipeline（#31）

- **三类基准**：RAG ≥100 / Agent ≥50 / Safety ≥50（共 ≥200 条）
- **CI 评测门禁**：PR 时自动跑，回退 >5% 则 block
- **双格式报告**：Markdown + JSON
- **无 LLM 降级**：无 `EVAL_API_KEY` 时跳过 live 用例
- **CLI**：`python eval/run.py run-eval` + `gate`

详见 [docs/phase-j-eval-pipeline.md](docs/phase-j-eval-pipeline.md)。

## Phase J — 反馈飞轮（#32）

- **反馈采集**：点赞/点踩 + 1-5 星评分 + 评论
- **质量监控**：滑动窗口聚合（满意度 + 差评数 + 平均分）
- **异常告警**：满意度骤降 / 差评激增 / 评分下滑
- **Bad Case → Eval**：自动入库到 `eval/baselines/bad_cases.jsonl`
- **Prompt 优化建议**：LLM 分析 Bad Case 生成建议
- **A/B 实验自动创建**：`FEEDBACK_LOOP_AUTO_EXPERIMENT=true` 时启用
- **REST API**：`/internal/feedback` + `/internal/quality` + `/internal/feedback-loop`

详见 [docs/phase-j-feedback-loop.md](docs/phase-j-feedback-loop.md)。

## Phase K — 对象存储接入（#33）

- **3 种后端**：`local`（回退）/ `s3`（boto3）/ `oss`（oss2）
- **统一接口**：`StorageBackend` ABC — put/get/delete/list/exists
- **预签名 URL**：S3/OSS 支持临时下载链接
- **REST API**：`POST /internal/storage/upload` + 下载 + 列表
- **配置切换**：`STORAGE_BACKEND=local|s3|oss`

详见 [docs/phase-k-object-storage.md](docs/phase-k-object-storage.md)。

## Phase K — K8s Helm Chart（#34）

- **一键部署**：`helm install ai-platform-lab deploy/helm/ai-platform-lab/`
- **5 个服务**：Gateway / Worker / Qdrant / Redis / Postgres
- **HPA 自动伸缩**：Gateway 2-10 / Worker 1-5（CPU 70%/80%）
- **Ingress + TLS**：可选 cert-manager
- **生产配置**：`values-prod.yaml`（3 replicas + 50Gi + 外部 DB）

详见 [docs/phase-k-helm.md](docs/phase-k-helm.md)。

## Phase K — 多 AZ 高可用（#35）

- **跨 AZ 部署**：`values-multi-az.yaml` overlay
- **Qdrant 读副本**：跨 AZ 分布，读请求分流
- **Redis Sentinel**：3 节点 quorum 故障转移
- **Postgres streaming replication**：主从复制 + 跨 AZ standby
- **PDB + NetworkPolicy**：自愿中断保护 + 网络隔离
- **Chaos 测试**：`deploy/k8s/chaos-test.yaml`（ChaosMesh 杀单 AZ 验证）

详见 [docs/phase-k-multi-az.md](docs/phase-k-multi-az.md)。

## Phase K — GPU 弹性调度（#36）

- **GPU 节点池**：nodeSelector `accelerator: nvidia` + taints
- **独立服务**：Embedding (8100) + Rerank (8200) GPU Deployment
- **多指标 HPA**：CPU 70% + GPU 70% + QPS
- **模型预热**：initContainer 预加载避免冷启动
- **成本看板**：Grafana JSON（GPU 小时费 + 节省率）
- **Dev/Prod 分层**：T4（便宜）+ A100（生产）

详见 [docs/phase-k-gpu-scheduling.md](docs/phase-k-gpu-scheduling.md)。

## 文档与代码导读

| 周次 | 接口 / 演示 | 构建思路与代码导读 |
|------|-------------|-------------------|
| 全路线 | [AI中台学习执行手册](docs/AI中台学习执行手册.md) | — |
| 架构 | [architecture.md](docs/architecture.md) | [roadmap.md](docs/roadmap.md) |
| Gap 分析 | [gap-analysis-diagram.md](docs/gap-analysis-diagram.md) | [roadmap-gantt.md](docs/roadmap-gantt.md) |
| Phase L 工程深度 | [phase-l-engineering-depth.md](docs/phase-l-engineering-depth.md) | [phase-l-priority-roi.md](docs/phase-l-priority-roi.md) |
| Phase L Console ✅ | [phase-l-console-integration.md](docs/phase-l-console-integration.md) | [demo-walkthrough.md](docs/demo-walkthrough.md) |
| Phase L Wave1 ✅ | [interview-narrative.md](docs/interview-narrative.md) | `./eval/platform_demo.sh` · `eval/sdk_smoke.py` |
| 本地 LLM 联调 | [local-llm-setup.md](docs/local-llm-setup.md) | [config/upstream_models.yaml](config/upstream_models.yaml) |
| Phase L Issues | [issues-backlog-phase-l.md](docs/issues-backlog-phase-l.md) | — |
| Phase M 增量索引 ✅ | [phase-m-incremental-index.md](docs/phase-m-incremental-index.md) | [issues-backlog-phase-m.md](docs/issues-backlog-phase-m.md) |
| Phase A 可内测 | [phase-a-internal-beta.md](docs/phase-a-internal-beta.md) | [phase-a-build-and-code-guide.md](docs/phase-a-build-and-code-guide.md) |
| Phase B1 计费 | [phase-b-small-production.md](docs/phase-b-small-production.md) | [phase-b-build-and-code-guide.md](docs/phase-b-build-and-code-guide.md) |
| Phase B2 并行 | [phase-b2-parallel.md](docs/phase-b2-parallel.md) | [phase-b-build-and-code-guide.md](docs/phase-b-build-and-code-guide.md) |
| Phase B3 rerank | [phase-b3-rerank-canary.md](docs/phase-b3-rerank-canary.md) | [phase-b-build-and-code-guide.md](docs/phase-b-build-and-code-guide.md) |
| Phase C 平台化 | [phase-c-platform.md](docs/phase-c-platform.md) | [phase-c-build-and-code-guide.md](docs/phase-c-build-and-code-guide.md) |
| Phase D 运维 | [phase-d-ops.md](docs/phase-d-ops.md) | [phase-d-build-and-code-guide.md](docs/phase-d-build-and-code-guide.md) · [phase-d-future-evolution.md](docs/phase-d-future-evolution.md) |
| Phase E Agent 效果 | [phase-e-agent-quality.md](docs/phase-e-agent-quality.md) | [phase-e-build-and-code-guide.md](docs/phase-e-build-and-code-guide.md) · [enterprise-ai-platform-sop.md](docs/enterprise-ai-platform-sop.md) |
| Phase F 能力中台 | [phase-f-prompt-registry.md](docs/phase-f-prompt-registry.md) 等 | [phase-f-build-and-code-guide.md](docs/phase-f-build-and-code-guide.md) |
| Phase G 模型服务 | [phase-g-semantic-cache.md](docs/phase-g-semantic-cache.md) | [phase-g-build-and-code-guide.md](docs/phase-g-build-and-code-guide.md) |
| Phase H Agent 高阶 | [phase-h-orchestrator.md](docs/phase-h-orchestrator.md) 等 | [phase-h-build-and-code-guide.md](docs/phase-h-build-and-code-guide.md) |
| Phase I 安全合规 | [phase-i-sandbox.md](docs/phase-i-sandbox.md) 等 | [phase-i-build-and-code-guide.md](docs/phase-i-build-and-code-guide.md) |
| Phase J 开发者体验 | [phase-j-python-sdk.md](docs/phase-j-python-sdk.md) 等 | [phase-j-build-and-code-guide.md](docs/phase-j-build-and-code-guide.md) |
| Phase K 生产基础设施 | [phase-k-helm.md](docs/phase-k-helm.md) 等 | [phase-k-build-and-code-guide.md](docs/phase-k-build-and-code-guide.md) |
| 大厂 SOP 对照 | [enterprise-ai-platform-sop.md](docs/enterprise-ai-platform-sop.md) | 按周次/Phase 的踩坑与 SOP |
| 第 1 周 Gateway | [week1-gateway.md](docs/week1-gateway.md) | [gateway-build-and-code-guide.md](docs/gateway-build-and-code-guide.md) |
| 第 2 周 RAG 管道 | [week2-rag-pipeline.md](docs/week2-rag-pipeline.md) | [rag-build-and-code-guide.md](docs/rag-build-and-code-guide.md) |
| 第 3 周 RAG 问答 | [week3-rag-query.md](docs/week3-rag-query.md) | [rag-query-build-and-code-guide.md](docs/rag-query-build-and-code-guide.md) |
| 第 4 周 Agent | [week4-agent-runtime.md](docs/week4-agent-runtime.md) | [agent-build-and-code-guide.md](docs/agent-build-and-code-guide.md) |
| 第 5 周 观测/评测 | [week5-observability-eval.md](docs/week5-observability-eval.md) | [observability-eval-build-and-code-guide.md](docs/observability-eval-build-and-code-guide.md) |
| 第 6 周 硬化 | [week6-hardening.md](docs/week6-hardening.md) | [hardening-build-and-code-guide.md](docs/hardening-build-and-code-guide.md) |

## 周次里程碑（Git Tag）

| Tag | Commit | 内容 |
|-----|--------|------|
| `week-1-gateway` | `f39b098` | 多租户 Gateway：鉴权、配额、chat 转发、trace_id |
| `week-2-rag-pipeline` | `2803a1b` | RAG：异步索引、kb 版本、Qdrant、`/internal/retrieve` |
| `week-3-rag-query` | `5dbcf68` | RAG 问答：`/v1/rag/query`、阈值拒答、citations、timings |
| `week-4-agent-runtime` | `617d535` | Agent：`/v1/agent/run`、工具白名单、会话、tool_calls 轨迹 |
| `week-5-observability-eval` | `66978a0` | OTel、/metrics、eval/run、load_smoke |
| `week-6-hardening` | `4368665` | Model Router、令牌桶、Compose、architecture/roadmap |
| `phase-a-internal-beta` | `1ce0806` | Redis 共享状态、Worker 队列、SQLite 审计、CI 门禁 |
| `phase-b1-billing` | `e54adbc` | Postgres token 计量、租户预算、billing API |
| `phase-b2-parallel` | `e621c7f` | 密钥 Env/Vault、RAG hybrid、OTel Collector 栈 |
| `phase-b3-rerank-canary` | `5536a05` | RAG rerank stub、kb 金丝雀路由 |
| `phase-c-platform` | `e7e96c2` | 供应商矩阵、Region、租户 API、工具市场 |
| `phase-d-ops` | `981ff89` | 熔断/Grafana、JWT/RBAC、控制台、账单 API |
| `phase-e-agent-quality` | `5163bf0` | Agent 轨迹评测、Tool-RAG、上下文预算、质量门、HITL/Shadow |

```bash
git fetch origin --tags
git show phase-e-agent-quality
```

## 目录说明

| 路径 | 说明 |
|------|------|
| `apps/gateway` | FastAPI 网关 |
| `apps/gateway/model_router.py` | 模型别名与降级 |
| `apps/gateway/rate_limit.py` | 租户令牌桶 |
| `packages/rag` | chunk / embedding / Qdrant |
| `packages/agent` | 工具注册表 / Agent 循环 / 会话 |
| `packages/observability` | trace / metrics / OTel |
| `config/models.yaml` | 模型别名与 fallback 链 |
| `packages/audit/` | SQLite 审计落库 |
| `packages/tasks/` | Redis 索引任务队列 |
| `packages/billing/` | Postgres token 计量与预算 |
| `packages/secrets/` | Env / Vault 密钥托管 |
| `packages/prompt/` | Prompt 版本化 + A/B 实验（YAML+JSON overrides） |
| `packages/memory/` | 长记忆持久化（Postgres + 进程内兜底 + 自动摘要） |
| `packages/mcp/` | MCP 真实集成（stdio/http 双协议 + JSON-RPC 2.0） |
| `packages/semantic_cache/` | 语义缓存（exact / semantic 双模式） |
| `packages/rag/bm25_index.py` | BM25 索引与混合检索 |
| `packages/rag/rerank.py` | 检索后 rerank（stub） |
| `packages/rag/routing.py` | kb 版本金丝雀分桶 |
| `config/tenants.yaml` | 三假租户 + 限速默认值 |
| `eval/baseline.jsonl` | RAG 评测用例（35 条） |
| `docs/` | 学习手册、周文档、架构与路线图 |

## 验收冒烟

```bash
python eval/acceptance_smoke.py        # 无 Key
python eval/acceptance_smoke.py --with-llm   # 已配置 Key 时
```
