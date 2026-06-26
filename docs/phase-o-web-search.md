# Phase O #91 — web_search 工具

> **Issue**：[#91](https://github.com/xingyun0812/ai-platform-lab/issues/91)  
> **状态**：✅ 已交付（含 `ddg` 真实检索 + Console 执行结果展示）

## 是什么

Agent 工具 **`web_search`**：搜索**公开互联网**信息，返回结构化 `{title, snippet, url}` 列表。企业内部知识库仍用 `get_kb_snippet`。

## 配置

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `WEB_SEARCH_MODE` | `mock` | `mock` \| `ddg` \| `http` |
| `WEB_SEARCH_URL` | 空 | http 模式 POST 端点 |
| `WEB_SEARCH_WEATHER_ENRICH` | `true` | 天气类 query 时调用 Open-Meteo 补充实时数值 |
| `WEB_SEARCH_TOP_K` | `3` | 默认条数 |
| `WEB_SEARCH_MAX_TOP_K` | `10` | 单次上限 |
| `WEB_SEARCH_TIMEOUT_SECONDS` | `10` | 请求超时 |

### 模式说明

| 模式 | 行为 |
|------|------|
| **mock** | 确定性假结果，CI 默认 |
| **ddg** | DuckDuckGo HTML 真实检索；失败返回 `error`（不 silently 假结果） |
| **http** | `POST WEB_SEARCH_URL`，body `{"query","top_k"}`；失败 fallback mock |

**天气增强**（`WEB_SEARCH_WEATHER_ENRICH=true`，默认开启）：query 含「天气/气温/预报」等词时，额外调用 [Open-Meteo](https://open-meteo.com/) 获取实时气温、湿度、风速，并作为**第一条搜索结果**插入，避免 LLM 只回复「请点链接查看」。

**http / ddg 响应格式**（http 上游）：`{"results": [{"title","snippet","url"}, ...]}`（也支持 `items` / 顶层 list）

## 工具路由与 Plan 执行

`config/agent_tool_routing.yaml` 含 `web_search` 意图（关键词：搜索、天气、新闻等）。

Plan 执行时，若 step 带 `tool_hint: web_search`，会**强制**将该工具并入候选集（避免「Plan 写了 web_search、执行却只有 calc」）。

## Console 展示

Agents 页 Task Planner：

- **执行结果**：仅 `final_message` 自然语言答复
- **执行结果 JSON**：完整响应，**默认折叠**

详见 [console-agent-planner.md](./console-agent-planner.md)。

## 租户 ACL

`config/tenants.yaml` 中 `allowed_tools` 须包含 `web_search` 才可调用；`admin` 空列表=全部工具；`demo-a` 默认未开放。

## 验证

```bash
python -m unittest tests.test_tools_web_search -v
```

## 与 search_web_stub 区别

| 工具 | 用途 |
|------|------|
| `search_web_stub` | Phase E decoy，Tool-RAG 路由演示 |
| `web_search` | Phase O 正式外部检索（mock/ddg/http） |
