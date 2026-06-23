# Phase M Issue Backlog — RAG 增量索引做满

> 规划：[phase-m-incremental-index.md](./phase-m-incremental-index.md)  
> **GitHub Milestone**：Phase M — 增量索引  
> **备份分支**：`backup/phase-m-pre-split`（直推前现场）

| Backlog | GitHub Issue | PR |
|---------|--------------|-----|
| M1 BM25 merge | [#63](https://github.com/xingyun0812/ai-platform-lab/issues/63) | #68 |
| M2 purge-source | [#64](https://github.com/xingyun0812/ai-platform-lab/issues/64) | #69 |
| M3 API + metrics | [#65](https://github.com/xingyun0812/ai-platform-lab/issues/65) | #70 |
| M4 demo 断言 | [#66](https://github.com/xingyun0812/ai-platform-lab/issues/66) | （本 PR） |

---

## M1 — BM25 按 source 增量 merge ✅ #63

**验收**：
- [x] `merge_source_into_index` 单测
- [x] `pipeline.py` 调用 `refresh_bm25_after_source_index`

---

## M2 — purge-source 清理向量+BM25 ✅ #64

**验收**：
- [x] `POST /internal/index/purge-source`
- [x] Console `DELETE` 文档同步清理

---

## M3 — API + Prometheus 暴露增量统计 ✅ #65

**验收**：
- [x] `IndexTaskView` 含 `new_chunks/updated_chunks/skipped_chunks`
- [x] `/metrics` 含 `rag_index_*` 指标

---

## M4 — demo 二次索引断言 ✅ #66

**验收**：
- [x] `platform_demo.sh --with-llm` 二次索引 `skipped_chunks >= 1`
