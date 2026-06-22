# Phase B2 并行交付：密钥 / 混合检索 / 可观测栈

Issues：[#7](https://github.com/xingyun0812/ai-platform-lab/issues/7) · [#8](https://github.com/xingyun0812/ai-platform-lab/issues/8) · [#10](https://github.com/xingyun0812/ai-platform-lab/issues/10)

---

构建思路、使用链路与逐文件代码说明见 [phase-b-build-and-code-guide.md](./phase-b-build-and-code-guide.md)。

## 1. 密钥托管（#7）

| 模式 | 配置 | 行为 |
|------|------|------|
| env（默认） | `SECRETS_PROVIDER=env` | `bearer_secret_ref: tenants/demo-a/bearer` → `SECRET_TENANTS_DEMO_A_BEARER` |
| vault | `SECRETS_PROVIDER=vault` + profile `vault` | KV v2 `secret/data/<ref>` 字段 `value` |

```bash
docker compose --profile vault up -d
export VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=dev-only-token
vault kv put secret/tenants/demo-a/bearer value=sk-tenant-demo-a-change-me
```

代码：`packages/secrets/provider.py`，租户加载见 `apps/gateway/tenants.py`。

---

## 2. RAG 混合检索（#8）

`config/rag.yaml`：

```yaml
retrieval_mode: hybrid   # 或 vector（默认，向后兼容）
bm25_top_k: 20
hybrid_rrf_k: 60
```

- 索引时写入 `data/rag/bm25/{kb_id}/v{version}.json`
- 查询时向量 top_k + BM25 top_k → **RRF 融合**
- `timings` 增加 `retrieve_vector_ms` / `retrieve_bm25_ms` / `fusion_ms`

代码：`packages/rag/bm25_index.py`、`packages/rag/hybrid.py`、`packages/rag/retrieval.py`。

---

## 3. 可观测栈（#10）

```bash
docker compose --profile observability up -d
# Jaeger UI: http://127.0.0.1:16686
# Prometheus: http://127.0.0.1:9090
```

Gateway 环境变量：

```bash
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317   # Compose 内
OTEL_CONSOLE_EXPORT=false   # 可选
```

配置：`config/otel-collector.yaml`、`config/prometheus.yml`。

---

## 验收清单

- [ ] 不配 Vault 时 `bearer_token` 鉴权与 Phase B1 一致
- [ ] `retrieval_mode=hybrid` 且重新索引后 query timings 含 bm25/fusion
- [ ] `--profile observability` 后 Jaeger 可见 `gateway.chat_completions` span
- [ ] Prometheus 能 scrape `gateway:8000/metrics`
