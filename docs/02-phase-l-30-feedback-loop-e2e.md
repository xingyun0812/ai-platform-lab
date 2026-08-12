# Phase L #61 — 反馈飞轮 E2E

> **状态**：✅ mock 闭环 + live 文档清单

## 链路

```mermaid
flowchart LR
    A["POST /internal/feedback<br/>点踩"] --> B["FeedbackStore"]
    B --> C["collect_bad_cases"]
    C --> D["ingest → bad_cases.jsonl"]
    D --> E["generate_prompt_suggestion"]
    E --> F["apply + experiment"]
    F --> G["Prompt A/B"]

    style A fill:#1e3a5f,stroke:#60a5fa,color:#e2e8f0
    style G fill:#14532d,stroke:#4ade80,color:#e2e8f0
```

## 命令

### Mock（CI / 无 Key）

```bash
python eval/feedback_loop_demo.py --mock
python -m pytest tests/test_feedback_loop_e2e.py -q
```

### Live（需 Gateway + admin token）

```bash
docker compose up -d gateway
export DEMO_ADMIN_TOKEN="Bearer sk-tenant-admin-change-me"
python eval/feedback_loop_demo.py --live --base-url http://127.0.0.1:8000
```

## Live Walkthrough Checklist

- [x] `curl /healthz` → 200
- [x] `POST /internal/feedback/` thumbs_down ×2（admin 租户）
- [x] `POST /internal/feedback-loop/cycle/admin` body `{"prompt_id":"rag_query"}`
  - [x] `bad_cases_collected` ≥ 1
  - [x] `ingested_count` ≥ 1
  - [x] `suggestion_id` 非空
- [ ] `POST /internal/feedback-loop/experiment/{suggestion_id}`（需 gateway 内 apply suggestion，或 `--auto-experiment`）
- [x] `eval/baselines/bad_cases.jsonl` 有新行（live ingest）

## 验收对照

| 项 | 文件 |
|----|------|
| Demo CLI | `eval/feedback_loop_demo.py` |
| E2E 单测 ≥10 | `tests/test_feedback_loop_e2e.py` |
| 管道实现 | `packages/feedback_loop/pipeline.py` |
| API | `apps/gateway/feedback_loop_routes.py` |
