# Phase O #91 — web_search 工具

> **Issue**：[#91](https://github.com/xingyun0812/ai-platform-lab/issues/91)  
> **状态**：✅ 已交付

## 是什么

Agent 工具 **`web_search`**：搜索**公开互联网**信息，返回结构化 `{title, snippet, url}` 列表。企业内部知识库仍用 `get_kb_snippet`。

## 配置

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `WEB_SEARCH_MODE` | `mock` | `mock` \| `http` |
| `WEB_SEARCH_URL` | 空 | http 模式 POST 端点 |
| `WEB_SEARCH_TOP_K` | `3` | 默认条数 |
| `WEB_SEARCH_MAX_TOP_K` | `10` | 单次上限 |
| `WEB_SEARCH_TIMEOUT_SECONDS` | `10` | http 超时 |

**http 模式**请求体：`{"query": "...", "top_k": N}`  
响应：`{"results": [{"title","snippet","url"}, ...]}`（也支持 `items` / 顶层 list）

http 失败时自动 **fallback mock**（`mode=mock_fallback`）。

## 租户 ACL

`config/tenants.yaml` 中 `allowed_tools` 须包含 `web_search` 才可调用；`demo-a` 默认未开放（注释说明可选添加）。

## 验证

```bash
python -m unittest tests.test_tools_web_search -v
```

## 与 search_web_stub 区别

| 工具 | 用途 |
|------|------|
| `search_web_stub` | Phase E decoy，Tool-RAG 路由演示 |
| `web_search` | Phase O 正式外部检索（mock/http） |
