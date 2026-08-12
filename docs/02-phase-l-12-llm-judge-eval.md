# Phase L #56 — LLM-as-Judge Eval

> **状态**：✅ keyword 默认 + 可选 llm_judge

## JSONL 字段

```json
{
  "id": "rag-001",
  "query": "...",
  "expected_keywords": ["RAG"],
  "grading": "llm_judge",
  "rubric": "Answer must be grounded in retrieved context."
}
```

- `grading`: `keyword`（默认）| `llm_judge`
- 无 `EVAL_API_KEY` / `LLM_API_KEY` 时自动 keyword
- Judge 失败降级 `keyword_fallback`

## 环境变量

```bash
EVAL_API_KEY=...
EVAL_JUDGE_MODEL=deepseek-v4-flash   # 可选，默认 DEFAULT_MODEL
LLM_BASE_URL=http://10.212.129.94:8090/v1
```

## 报告

`EvalReport.grading_stats` 统计各 grading 模式次数；Markdown 报告 Summary 行展示。

## 测试

```bash
python3 tests/test_eval_grader.py
python3 tests/test_eval_pipeline.py
```
