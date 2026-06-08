# ai-platform-lab

最小 **AI 中台** 实验仓库（与 [《AI中台学习执行手册》](docs/AI中台学习执行手册.md) 配套）。当前完成 **第 1～6 周** + **Phase A 可内测**：Gateway、RAG、Agent、观测评测、硬化，以及 Redis 共享状态、Worker 队列、SQLite 审计、CI 门禁。

## 15 分钟快速跑通

```bash
cd /Users/zhangyue/IdeaProjects/ai-platform-lab
cp .env.example .env          # 可选：填 LLM_API_KEY 以联调 chat/RAG/agent
docker compose up -d --build  # redis + gateway :8000 + worker + qdrant :6333
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
python eval/acceptance_smoke.py
```

详见 [docs/phase-a-internal-beta.md](docs/phase-a-internal-beta.md)。

## 文档与代码导读

| 周次 | 接口 / 演示 | 构建思路与代码导读 |
|------|-------------|-------------------|
| 全路线 | [AI中台学习执行手册](docs/AI中台学习执行手册.md) | — |
| 架构 | [architecture.md](docs/architecture.md) | [roadmap.md](docs/roadmap.md) |
| Phase A 可内测 | [phase-a-internal-beta.md](docs/phase-a-internal-beta.md) | — |
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

```bash
git fetch origin --tags
git show phase-a-internal-beta
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
| `config/tenants.yaml` | 三假租户 + 限速默认值 |
| `eval/baseline.jsonl` | RAG 评测用例（35 条） |
| `docs/` | 学习手册、周文档、架构与路线图 |

## 验收冒烟

```bash
python eval/acceptance_smoke.py        # 无 Key
python eval/acceptance_smoke.py --with-llm   # 已配置 Key 时
```
