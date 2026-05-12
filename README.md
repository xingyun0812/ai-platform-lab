# ai-platform-lab

最小 **AI 中台** 实验仓库（与 Obsidian《AI中台学习执行手册》配套）。当前完成 **第 1 周骨架**：多租户 LLM Gateway（鉴权、配额、非流式转发、trace_id）。

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

## 目录说明

| 路径 | 说明 |
|------|------|
| `apps/gateway` | FastAPI 网关 |
| `apps/worker` | 异步任务占位（第 2 周） |
| `packages/contracts` | 请求/错误体模型 |
| `packages/observability` | `trace_id` 中间件 |
| `config/tenants.yaml` | 三假租户 |
| `eval/` | 评测集（占位） |

## 后续周次

按《AI中台学习执行手册》扩展 RAG 管道、Agent 运行时、观测与评测等模块。
