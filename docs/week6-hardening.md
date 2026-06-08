# 第 6 周：硬化与平台叙事

学习计划见 [AI中台学习执行手册](./AI中台学习执行手册.md) 第 6 周。  
构建思路见 [hardening-build-and-code-guide.md](./hardening-build-and-code-guide.md)。

---

## 目标

- **Model Router**：租户模型别名 + 上游失败降级
- **限流**：租户级令牌桶（内存）
- **Docker Compose**：`docker compose up` 启动 gateway + Qdrant
- **平台文档**：`architecture.md` + `roadmap.md`

---

## 15 分钟主路径（README 精简版）

### 1. 准备环境（约 3 分钟）

```bash
cd /Users/zhangyue/IdeaProjects/ai-platform-lab
cp .env.example .env
# 编辑 .env 填写 LLM_API_KEY（可选：无 Key 也能跑健康检查与限流演示）
docker compose up -d --build
curl -s http://127.0.0.1:8000/healthz
```

### 2. 租户 Chat（约 2 分钟）

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: demo-a" \
  -H "Authorization: Bearer sk-tenant-demo-a-change-me" \
  -d '{"model":"chat-fast","messages":[{"role":"user","content":"你好"}]}'
```

`chat-fast` 在 `config/models.yaml` 中映射为 `gpt-4o-mini`。

### 3. RAG 索引 + 问答（约 5 分钟，需 LLM Key）

```bash
curl -s http://127.0.0.1:8000/internal/index \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"kb_id":"lab-demo","version":1,"source_uri":"samples/hello.txt"}'

# 轮询任务至 succeeded 后：
curl -s http://127.0.0.1:8000/v1/rag/query \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"tenant_id":"admin","kb_id":"lab-demo","version":1,"query":"RAG 是什么"}'
```

### 4. Agent + 评测（约 5 分钟，需 Key）

```bash
curl -s http://127.0.0.1:8000/v1/agent/run \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"tenant_id":"admin","session_id":"w6-demo","messages":[{"role":"user","content":"用 calc 算 6*7"}]}'

python eval/run.py run
```

---

## Model Router

配置：`config/models.yaml`

| 能力 | 说明 |
|------|------|
| `aliases` | 如 `chat-fast` → `gpt-4o-mini` |
| `fallback_chains` | 主模型 429/5xx/网络错误时按链重试 |
| 租户 `default_model` | 请求未带 model 时使用别名或真实名 |

Chat 降级成功时，响应 JSON 可能含 `_platform.fallback_used` 与 `model_used`（仅降级时写入，避免污染上游标准字段）。

---

## 令牌桶限流

配置：`config/tenants.yaml`

```yaml
defaults:
  rate_limit_rps: 20
  rate_limit_burst: 40
tenants:
  demo-b:
    rate_limit_rps: 2
    rate_limit_burst: 2
```

超限返回 **429**，`error.code=RATE_LIMIT_EXCEEDED`，`detail.retry_after_seconds` 供客户端退避。

演示（快速连打 3 次 healthz 以外的写接口，或对 demo-b 连发 chat）：

```bash
for i in 1 2 3; do
  curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "X-Tenant-Id: demo-b" \
    -H "Authorization: Bearer sk-tenant-demo-b-change-me" \
    -d '{"messages":[{"role":"user","content":"hi"}]}'
done
```

预期：前 2 次可能 503（无 Key）或 200（有 Key），第 3 次 **429**。

---

## Docker Compose

```bash
docker compose up -d --build      # gateway + qdrant
docker compose logs -f gateway
docker compose down
```

可选 worker profile（占位，索引仍在 gateway）：

```bash
docker compose --profile worker up -d
```

---

## 平台叙事文档

| 文档 | 用途 |
|------|------|
| [architecture.md](./architecture.md) | 分层图 + 三条核心数据流 |
| [roadmap.md](./roadmap.md) | 已知限制与演进阶段 |

---

## 验收清单

- [ ] `docker compose up -d --build` 后 `/healthz` 返回 ok
- [ ] `demo-a` 使用别名 `chat-fast` 可调 chat（或 503 无 Key）
- [ ] `demo-b` 超速触发 `RATE_LIMIT_EXCEEDED`
- [ ] 能打开 `architecture.md` 对照口述租户 / RAG / Agent / eval
- [ ] `python eval/acceptance_smoke.py` 通过（无 Key 时 blocked 项可忽略）

---

## 相关 Tag

`week-6-hardening` — 第 6 周交付点。
