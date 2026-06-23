# Phase M Issue Backlog — RAG 增量索引做满

> 规划：[phase-m-incremental-index.md](./phase-m-incremental-index.md)

---

## M1 — BM25 按 source 增量 merge

**目标**：索引单文件时不再 `scroll` 全库重建 BM25。

**验收**：
- [ ] `merge_source_into_index` 单测
- [ ] `pipeline.py` 调用 `refresh_bm25_after_source_index`

---

## M2 — purge-source 清理向量+BM25

**目标**：删除文档时同步 Qdrant + BM25；Console DELETE 可用。

**验收**：
- [ ] `POST /internal/index/purge-source`
- [ ] `DELETE /internal/console/rag/knowledge-bases/{kb_id}/documents/{doc_id}`

---

## M3 — API + Prometheus 暴露增量统计

**验收**：
- [ ] `IndexTaskView` 含 `new_chunks/updated_chunks/skipped_chunks`
- [ ] `/metrics` 含 `rag_index_*` 指标

---

## M4 — demo 二次索引断言

**验收**：
- [ ] `platform_demo.sh --with-llm` 二次索引 `skipped_chunks >= 1`
