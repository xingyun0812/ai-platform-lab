# Phase P — 多模态 Embedding

> **状态**：进行中（P1 ✅ · P2 ✅ · P3 Console/SDK 待做）  
> **前置**：Phase G Embedding 独立服务 ✅ · Phase O ✅  
> **Tag**（完成后）：`phase-p-multimodal`  
> **非目标**：自托管 CLIP 训推、视频/音频 embedding、分布式向量缓存

---

## 1. 目标与一句话讲法

**目标**：在现有 `packages/embedding/` 上扩展 **text + image** 统一输入，支持 OpenAI 风格 `inputs`、stub 确定性向量、RAG 图文索引（后续）。

**面试一句话**：

> Phase P 把 Embedding 从「纯文本」升级为 **多模态 inputs**（text / image_url / image_base64），API 向后兼容 `texts`；stub 可离线跑通 CI，RAG 可索引「图 + 说明」混合知识。

---

## 2. 架构

```mermaid
%%{init: {'theme': 'dark', 'themeVariables': { 'primaryColor': '#1e3a5f', 'primaryTextColor': '#e6edf3', 'lineColor': '#8b949e'}}}%%
flowchart LR
    API["POST /internal/embeddings/embed"]
    NORM["multimodal.normalize_items"]
    VAL["validate modalities"]
    CACHE["LRU cache by fingerprint"]
    PROV["Stub / OpenAI provider"]
    VEC["embeddings[]"]

    API --> NORM --> VAL --> CACHE --> PROV --> VEC
```

| 输入 type | 说明 | modality |
|-----------|------|----------|
| `text` | 纯文本 | text |
| `image_url` | HTTP(S) 或 data URL | image |
| `image_base64` | base64 + mime | image |

---

## 3. Issue 拆分

| Issue | 标题 | 状态 | 说明 |
|-------|------|------|------|
| **P1** | 多模态 inputs + stub + API | ✅ | PR #109 |
| **P2** | RAG 图文 chunk 索引 | ✅ | PR #109 · #110 |
| **P3** | Console / SDK embed inputs | ⏳ | console-v2 + Python SDK |
| **P4** | eval 门禁 + tag | ⏳ | `multimodal_embedding_gate` |

---

## 4. P2 验收（RAG 图文索引）

- [x] `packages/rag/multimodal_index.py` — 图片检测 + caption sidecar
- [x] `TextChunk.modality` + `content_fingerprint` 增量跳过
- [x] `packages/rag/embeddings.py` — `embed_rag_chunks` / image_base64
- [x] `apps/gateway/rag/pipeline.py` — 图片走 `chunk_image_file`
- [x] `RAG_MULTIMODAL_EMBEDDING_MODEL` 配置项
- [x] `tests/test_rag_multimodal_index.py`
- [x] `eval/rag_multimodal_smoke.py`
- [x] `samples/chart.png` + caption sidecar

---

## 5. P1 验收

- [x] `packages/embedding/multimodal.py` 归一化 + 指纹
- [x] `EmbeddingModel.modalities` + `EmbeddingRequest.inputs`
- [x] `POST /embed` 支持 `inputs`（`texts` 仍兼容）
- [x] `config/embedding_models.yaml` → `stub-multimodal`
- [x] `tests/test_multimodal_embedding.py`
- [x] `eval/multimodal_embedding_smoke.py`

---

## 6. API 示例

```bash
curl -s -X POST http://127.0.0.1:8000/internal/embeddings/embed \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{
    "model_id": "stub-multimodal",
    "inputs": [
      {"type": "text", "text": "季度销售趋势图"},
      {"type": "image_url", "url": "https://example.com/chart.png"}
    ]
  }'
```

---

## 7. 验证

```bash
python -m unittest tests.test_multimodal_embedding -q
python eval/multimodal_embedding_smoke.py
python eval/rag_multimodal_smoke.py
python -m unittest tests.test_embedding -q   # 回归
```

---

## 8. 诚实边界

| 项 | 说明 |
|----|------|
| OpenAI 真多模态 embed | 多数网关仅 text；含图时降级为 content 或 text 占位 |
| 视频/音频 | 不在 P1 范围 |
| 分布式缓存 | 仍进程内 LRU |
| CLIP 本地推理 | 走 external provider，不自训 |

---

## 9. 相关文档

| 文档 | 用途 |
|------|------|
| [phase-g-embedding.md](./phase-g-embedding.md) | Embedding 服务基线 |
| [issues-backlog-phase-p.md](./issues-backlog-phase-p.md) | Issue 正文 |

---

*规划稿 · 2026-06-09 · Phase P 启动*
