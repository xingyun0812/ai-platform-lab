# Phase L #55 — RAG 增量索引

> **状态**：✅ chunk 指纹跳过未变 embed

## 原理

1. 每个 chunk 写入 Qdrant payload：`content_hash = sha256(text)[:16]`
2. 二次索引同一 `source_uri` 时对比 `offset + content_hash`
3. 未变 chunk **跳过 embed/upsert**；变更/新增才 embed；删除的 offset 移除向量

## 任务结果字段

`IndexTaskRecord` 增加：

| 字段 | 含义 |
|------|------|
| `new_chunks` | 新增 chunk 数 |
| `updated_chunks` | 内容变更 re-embed 数 |
| `skipped_chunks` | 指纹相同跳过数 |
| `chunks_indexed` | 三者之和（该 source 有效 chunk 总数） |

## 代码

| 模块 | 职责 |
|------|------|
| `packages/rag/indexing.py` | `content_hash` + `plan_incremental_index` |
| `packages/rag/vector_store.py` | `list_source_chunks` / `delete_points` |
| `apps/gateway/rag/pipeline.py` | 索引任务增量逻辑 |

## 验证

```bash
python3 tests/test_incremental_index.py

# live（需 Qdrant + embedding）
curl -X POST .../internal/index -d '{"kb_id":"lab-demo","version":1,"source_uri":"samples/hello.txt"}'
# 二次相同 source：任务 skipped_chunks >= 1
```

## BM25

向量增量后，对该 `source_uri` **差量 merge** BM25（Phase M）；删除 source 时同步清理 BM25 条目。
