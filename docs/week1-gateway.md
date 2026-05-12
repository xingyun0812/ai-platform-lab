# 第 1 周：Gateway

详见仓库根目录 [README.md](../README.md)。

验收要点：

- `GET /healthz` 返回 `ok`
- `POST /v1/chat/completions` 在配置 `LLM_API_KEY` 后可转发上游
- 未配置 Key 时返回 `UPSTREAM_NOT_CONFIGURED`，且不消耗配额（见实现）
- 错误体为 `{ "error": { "code", "message", "trace_id", "detail?" } }`
