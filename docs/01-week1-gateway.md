# 第 1 周：Gateway

学习计划与执行节奏见 [AI中台学习执行手册](./90-ai-learning-handbook.md)。  
构建思路、使用链路与逐文件代码说明见 [gateway-build-and-code-guide.md](./01-week1-guide.md)。  
第 2 周 RAG 管道见 [week2-rag-pipeline.md](./01-week2-rag-pipeline.md)、[rag-build-and-code-guide.md](./01-week2-guide.md)。  
第 3 周 RAG 问答见 [week3-rag-query.md](./01-week3-rag-query.md)、[rag-query-build-and-code-guide.md](./01-week3-guide.md)。  
第 4 周 Agent 见 [week4-agent-runtime.md](./01-week4-agent-runtime.md)、[agent-build-and-code-guide.md](./01-week4-guide.md)。  
第 5 周观测/评测见 [week5-observability-eval.md](./01-week5-observability-eval.md)。  
详见仓库根目录 [README.md](../README.md)。

验收要点：

- `GET /healthz` 返回 `ok`
- `POST /v1/chat/completions` 在配置 `LLM_API_KEY` 后可转发上游
- 未配置 Key 时返回 `UPSTREAM_NOT_CONFIGURED`，且不消耗配额（见实现）
- 错误体为 `{ "error": { "code", "message", "trace_id", "detail?" } }`
