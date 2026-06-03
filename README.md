# ai-platform-lab

最小 **AI 中台** 实验仓库（与 [《AI中台学习执行手册》](docs/AI中台学习执行手册.md) 配套）。当前完成 **第 1 周 Gateway** + **第 2 周 RAG 数据管道** + **第 3 周 RAG 问答**（`/v1/rag/query`、阈值拒答、引用与分阶段耗时）。

## 环境

- Python **3.11+**
- 申请到 OpenAI 兼容 API 后，在项目根目录复制环境变量：

```bash
cd /Users/zhangyue/IdeaProjects/ai-platform-lab
cp .env.example .env
# 编辑 .env：填写 LLM_API_KEY；按需改 LLM_BASE_URL、DEFAULT_MODEL
```

## 安装与启动

```bash
cd /Users/zhangyue/IdeaProjects/ai-platform-lab
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -U pip
pip install -e .
uvicorn apps.gateway.main:app --reload --host 127.0.0.1 --port 8000
```

健康检查：

```bash
curl -s http://127.0.0.1:8000/healthz
```

## 租户与鉴权

配置见 `config/tenants.yaml`。请求 **必须** 同时携带：

- `X-Tenant-Id`：与 yaml 中键一致（如 `demo-a`）
- `Authorization: Bearer <与 yaml 中 bearer_token 一致>`

可选：本地覆盖 `config/tenants.local.yaml`（已加入 `.gitignore`），用于改 token 而不改主配置。

## 调用示例（非流式）

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "用一句话介绍你自己"}]
  }'
```

未配置 `LLM_API_KEY` 时，返回 **503** 且 `error.code=UPSTREAM_NOT_CONFIGURED`。

## RAG 管道（第 2 周）

1. 启动向量库：`docker compose --profile vectors up -d`
2. `.env` 中已配置 `LLM_API_KEY`（embedding 与对话共用上游）
3. 提交索引、查任务、检索见 [docs/week2-rag-pipeline.md](docs/week2-rag-pipeline.md)

示例（admin 租户）：

```bash
curl -s http://127.0.0.1:8000/internal/index \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"kb_id":"lab-demo","version":1,"source_uri":"samples/hello.txt"}'
```

## RAG 问答（第 3 周）

在已完成索引的前提下：

```bash
curl -s http://127.0.0.1:8000/v1/rag/query \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"tenant_id":"admin","kb_id":"lab-demo","version":1,"query":"RAG 数据管道是什么"}'
```

详见 [docs/week3-rag-query.md](docs/week3-rag-query.md)。评测用例见 [eval/baseline.jsonl](eval/baseline.jsonl)。

## 文档与代码导读

| 周次 | 接口 / 演示 | 构建思路与代码导读 |
|------|-------------|-------------------|
| 全路线 | [AI中台学习执行手册](docs/AI中台学习执行手册.md) | — |
| 第 1 周 Gateway | [week1-gateway.md](docs/week1-gateway.md) | [gateway-build-and-code-guide.md](docs/gateway-build-and-code-guide.md) |
| 第 2 周 RAG 管道 | [week2-rag-pipeline.md](docs/week2-rag-pipeline.md) | [rag-build-and-code-guide.md](docs/rag-build-and-code-guide.md) |
| 第 3 周 RAG 问答 | [week3-rag-query.md](docs/week3-rag-query.md) | [rag-query-build-and-code-guide.md](docs/rag-query-build-and-code-guide.md) |

- **周文档**：验收要点、curl 演示、API 说明。  
- **导读专篇**：分层与搭建顺序、使用链路、逐文件读代码、错误码与自测用例（适合复习或给他人讲解）。

## 周次里程碑（Git Tag）

每周收尾在对应 commit 上打了 **annotated tag**，便于按周 checkout 或对比 diff：

| Tag | Commit | 内容 |
|-----|--------|------|
| `week-1-gateway` | `f39b098` | 多租户 Gateway：鉴权、配额、chat 转发、trace_id |
| `week-2-rag-pipeline` | `2803a1b` | RAG：异步索引、kb 版本、Qdrant、`/internal/retrieve` |
| `week-3-rag-query` | （见 `git show week-3-rag-query`） | RAG 问答：`/v1/rag/query`、阈值拒答、citations、timings |

```bash
# 查看某周 tag 说明
git show week-3-rag-query

# 切换到该周代码（ detached HEAD，看完回到 main）
git switch --detach week-3-rag-query
git switch main

# 两周之间的提交（示例：第 2 周 → 第 3 周）
git log week-2-rag-pipeline..week-3-rag-query --oneline

# 首次克隆后拉取远程 tag
git fetch origin --tags
```

推送 tag 到远程（维护者）：

```bash
git push origin main
git push origin week-1-gateway week-2-rag-pipeline week-3-rag-query
# 或一次性：git push origin main --tags
```

## 目录说明

| 路径 | 说明 |
|------|------|
| `apps/gateway` | FastAPI 网关 |
| `apps/worker` | Worker 说明入口（索引任务当前在 gateway 后台执行） |
| `packages/rag` | chunk / embedding / Qdrant |
| `packages/contracts` | 请求/错误体/RAG 模型 |
| `data/rag` | 待索引样例文本 |
| `config/rag.yaml` | chunk / min_score 等默认参数 |
| `config/rag_prompt.txt` | RAG 问答 Prompt 模板 |
| `packages/observability` | `trace_id` 中间件 |
| `config/tenants.yaml` | 三假租户 |
| `eval/baseline.jsonl` | RAG 评测用例（35 条，第 5 周跑批） |
| `docs/` | 学习手册、周文档、导读专篇 |

## 后续周次

按 [《AI中台学习执行手册》](docs/AI中台学习执行手册.md) 扩展 Agent 运行时、观测与评测等模块。
