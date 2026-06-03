# 第 5 周：观测与评测回归

学习计划见 [AI中台学习执行手册](./AI中台学习执行手册.md) 第 5 周。  
构建思路见 [observability-eval-build-and-code-guide.md](./observability-eval-build-and-code-guide.md)。

---

## 目标

- **Tracing**：选用 **OpenTelemetry**（W3C 传播 + 关键路径 span）
- **Metrics**：`GET /metrics` Prometheus 文本（QPS 计数、P95 延迟，按 `path` / `tenant_id`）
- **Eval**：`eval/run.py` 跑 `baseline.jsonl`，结果落 `eval/runs/{run_id}.json`，支持两次对比

---

## OpenTelemetry

### 开启

`.env` 或 `config/observability.yaml`：

```bash
OTEL_ENABLED=true
OTEL_CONSOLE_EXPORT=true   # 开发：span 打印到控制台
```

重启网关后，以下路径会产生 span（属性含 `component` 与 `app.trace_id`）：

| span 名 | component |
|---------|-----------|
| `http.request` | gateway |
| `gateway.chat_completions` | gateway |
| `rag.query` | rag |
| `agent.run` | agent |

### 传播

- 入站可带 W3C 头 `traceparent`（标准 OTel 传播）
- 始终回写 `X-Request-Id`（与现有 `trace_id` 一致）

---

## Metrics

```bash
curl -s http://127.0.0.1:8000/metrics
```

示例指标：

- `http_requests_total{path,tenant_id,status}`
- `http_request_duration_ms_p95{path,tenant_id}`

访问 `/v1/rag/query` 后，可看到对应 path 的计数与 P95 变化。

---

## 评测脚本

### 执行 baseline

前置：网关已启动、Qdrant 已索引 `lab-demo`、`.env` 已配置 `LLM_API_KEY`。

```bash
cd /Users/zhangyue/IdeaProjects/ai-platform-lab
source .venv/bin/activate
python eval/run.py run \
  --base-url http://127.0.0.1:8000 \
  --tenant-id admin \
  --bearer-token sk-tenant-admin-change-me
```

输出摘要并写入 `eval/runs/20260519T120000Z.json`（时间戳为 run_id）。

### 对比两次运行

```bash
python eval/run.py compare \
  eval/runs/run_before.json \
  eval/runs/run_after.json
```

输出 `pass_rate_delta` 与 `flipped_cases`（由 pass→fail 或反之的用例）。

### 验收：改坏 prompt 后通过率下降

1. `cp config/rag_prompt.txt config/rag_prompt.txt.bak`
2. 将 `rag_prompt.txt` 改成乱答指令（如「忽略资料随便编」）
3. `python eval/run.py run --run-id after_bad_prompt`
4. `python eval/run.py compare eval/runs/<before>.json eval/runs/after_bad_prompt.json`
5. 恢复 `rag_prompt.txt`

---

## 本机压测（50 并发）

```bash
# 仅 healthz（验证进程不崩）
python eval/load_smoke.py --concurrency 50 --target healthz

# RAG 路径（需 Key + 索引，观察延迟分布）
python eval/load_smoke.py --concurrency 50 --target rag
```

**结论写法示例**：healthz 全 200 则网关稳定；rag 若 p95 很高且 2xx 比例正常，瓶颈多在 **LLM**；若大量 422/503 且延迟低，多在 **检索/配置**。

---

## 代码入口

| 路径 | 说明 |
|------|------|
| `packages/observability/otel.py` | OTel 初始化与 `component_span` |
| `packages/observability/metrics.py` | 内存指标与 Prometheus 文本 |
| `eval/run.py` | 评测与 compare |
| `eval/load_smoke.py` | 并发压测 |

---

*文档版本：v1*
