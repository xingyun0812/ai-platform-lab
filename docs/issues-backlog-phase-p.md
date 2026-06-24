# Phase P Issue Backlog — 多模态 Embedding

> 规划：[phase-p-multimodal-embedding.md](./phase-p-multimodal-embedding.md)  
> **Milestone**：Phase P — Multimodal Embedding  
> **Tag**：`phase-p-multimodal`  
> **前置**：Phase G Embedding 服务、Phase O 收尾

| Backlog | GitHub Issue | 状态 |
|---------|--------------|------|
| P1 多模态 inputs + stub | #108 | ✅ |
| P2 RAG 图文索引 | #110 | 🚧 |
| P3 Console / SDK | （待创建） | ⏳ |
| P4 eval 门禁 + tag | （待创建） | ⏳ |

---

## P1 — 多模态 Embedding 输入层

**标题**：`[Phase P] P1 Multimodal embedding inputs — text + image stub`

**目标**：`inputs` API、modalities 校验、stub-multimodal、单测。

**验收**：
- [x] `packages/embedding/multimodal.py`
- [x] `/internal/embeddings/embed` 支持 `inputs`
- [x] `tests/test_multimodal_embedding.py`
- [x] `eval/multimodal_embedding_smoke.py`

**预估工期**：2～3d

---

## P2 — RAG 图文 chunk

**标题**：`[Phase P] P2 RAG multimodal index — image source + caption`

**目标**：索引 pipeline 支持 image URI + 可选 caption，写入同一向量空间。

**验收**：
- [x] `source_uri` 图片类型检测
- [x] chunk metadata `modality=image`
- [x] 单测 + `eval/rag_multimodal_smoke.py`

**依赖**：P1

---

## P3 — Console / SDK

**标题**：`[Phase P] P3 Console SDK multimodal embed`

**依赖**：P1

---

## P4 — 收尾

**标题**：`[Phase P] P4 Docs gate + tag phase-p-multimodal`

**依赖**：P1～P3
