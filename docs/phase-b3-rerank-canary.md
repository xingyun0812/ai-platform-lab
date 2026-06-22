# Phase B3：RAG rerank + kb 版本金丝雀

Issue：[#9](https://github.com/xingyun0812/ai-platform-lab/issues/9)

---

构建思路、使用链路与逐文件代码说明见 [phase-b-build-and-code-guide.md](./phase-b-build-and-code-guide.md)。

## 1. Rerank（检索后重排）

`config/rag.yaml`：

```yaml
rerank_enabled: true
rerank_mode: stub    # 词面重合，免 GPU
rerank_top_n: 10
```

链路：`retrieve` → **rerank** → `min_score` 过滤 → LLM。

- 代码：`packages/rag/rerank.py`
- `timings.rerank_ms` 写入 `/v1/rag/query` 响应
- stub 模式会把与 query 词面更相关的 chunk 排到前面

---

## 2. kb 版本金丝雀

`config/rag.yaml` → `kb_routing`：

```yaml
kb_routing:
  lab-demo:
    stable_version: 1
    canary_version: 2
    canary_percent: 30   # 0 = 全量 stable，用于回滚
```

| 场景 | 行为 |
|------|------|
| 请求体带 `version` | 固定该版本（`pinned`） |
| `canary_percent=0` | 100% `stable_version` |
| `canary_percent=30` | `hash(tenant_id:query) % 100 < 30` → canary |

查询 API 响应 `_platform.routing`：

```json
{"route": "canary", "bucket": 12, "version": 2}
```

查看路由配置：

```bash
curl -s -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  http://127.0.0.1:8000/internal/kb/lab-demo/routing | jq .
```

**回滚**：把 `canary_percent` 设为 `0`，无需重新索引。

代码：`packages/rag/routing.py`、`apps/gateway/rag/pipeline.py`。

---

## 3. 验收与评测

```bash
# 冒烟（含 PB3 rerank / canary 单测）
python eval/acceptance_smoke.py

# 金丝雀命中率模拟（无需 Gateway）
python eval/canary_stats.py --kb-id lab-demo --samples 1000

# 对比 rerank 前后 pass_rate（需 LLM Key + 已跑两次 eval）
python eval/run.py run --run-id before-rerank
# 开启 rerank_enabled 后重启 gateway
python eval/run.py run --run-id after-rerank
python eval/run.py compare eval/runs/before-rerank.json eval/runs/after-rerank.json
```

---

## 架构

```mermaid
flowchart LR
  Q["/v1/rag/query"]
  R["retrieve\nvector/hybrid"]
  RR["rerank stub"]
  F["min_score"]
  L["LLM"]
  Q --> RV["resolve_query_version\n金丝雀分桶"]
  RV --> R --> RR --> F --> L

  style R fill:#1a2a3a,stroke:#60a5fa,color:#e2e8f0
  style RR fill:#1a3a2a,stroke:#4ade80,color:#e2e8f0
  style RV fill:#2a1a3a,stroke:#c084fc,color:#e2e8f0
```
