# ai-platform-lab

最小 **AI 中台** 实验仓库（与 [《AI中台学习执行手册》](docs/AI中台学习执行手册.md) 配套）。当前完成 **第 1 周 Gateway** + **第 2 周 RAG 数据管道**（异步索引、kb 版本、Qdrant、对内检索）。

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

## 目录说明

| 路径 | 说明 |
|------|------|
| `apps/gateway` | FastAPI 网关 |
| `apps/worker` | Worker 说明入口（索引任务当前在 gateway 后台执行） |
| `packages/rag` | chunk / embedding / Qdrant |
| `packages/contracts` | 请求/错误体/RAG 模型 |
| `data/rag` | 待索引样例文本 |
| `config/rag.yaml` | chunk 默认参数 |
| `packages/observability` | `trace_id` 中间件 |
| `config/tenants.yaml` | 三假租户 |
| `eval/` | 评测集（占位） |
| `docs/gateway-build-and-code-guide.md` | Gateway 构建思路与代码导读（回顾 / 讲解用） |
| `docs/week2-rag-pipeline.md` | 第 2 周 RAG 数据流、API、演示命令 |
| `docs/rag-build-and-code-guide.md` | 第 2 周 RAG 构建思路与代码导读（回顾 / 讲解用） |
| `docs/AI中台学习执行手册.md` | 全 8 周学习路线（与 Obsidian 笔记同步副本） |

## 后续周次

按 [《AI中台学习执行手册》](docs/AI中台学习执行手册.md) 扩展 RAG 服务化、Agent 运行时、观测与评测等模块。
