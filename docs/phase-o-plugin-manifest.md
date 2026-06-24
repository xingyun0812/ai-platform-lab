# Phase O #90 — Plugin Manifest 作者指南

> **Issue**：[#90](https://github.com/xingyun0812/ai-platform-lab/issues/90)

## 是什么

**Plugin Manifest** 是轻量 YAML 插件：把 Agent 工具声明放在 `config/plugins/*.yaml`，Gateway 启动时注册到 `ToolRegistry`。与 **MCP**（远程 Server）互补：MCP = 远程；Plugin = 本地 manifest。

## 最小示例

见 [`config/plugins/demo_echo.yaml`](../config/plugins/demo_echo.yaml)：

```yaml
name: demo_echo
description: Echo input text back
enabled: true
parameters_schema:
  type: object
  properties:
    text:
      type: string
      description: Text to echo back
  required:
    - text
handler:
  type: builtin
  name: echo
```

## Handler 类型

| 类型 | YAML | 说明 |
|------|------|------|
| **builtin** | `handler: echo` 或 `type: builtin` + `name: echo` | 内置 handler（当前仅 `echo`） |
| **http** | `type: http` + `url` + 可选 `method` / `timeout_seconds` | POST JSON arguments 到 HTTP 端点 |

## 租户 ACL

插件注册后，**租户 `allowed_tools` 仍生效**：

- `demo-a` 仅 `calc` + `get_kb_snippet` → 调 `demo_echo` 会被 Runner 403
- `admin` 空列表 → 可用全部工具（含插件）

## 配置

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `AGENT_PLUGINS_ENABLED` | `true` | 关闭则跳过插件目录 |
| `AGENT_PLUGINS_CONFIG_DIR` | `./config/plugins` | YAML 目录 |

## 命名规则

- `name` 不得与内置工具（`calc`、`get_kb_snippet` 等）重复
- 同目录多个 YAML 不得重名（后者跳过并打 log）

## 验证

```bash
python -m unittest tests.test_agent_plugins -v
```

## 扩展 builtin handler

1. 在 `packages/agent/plugins/handlers.py` 实现 async handler
2. 注册到 `BUILTIN_PLUGIN_HANDLERS`
3. 新建 YAML 引用 `handler.name`

HTTP 插件无需改代码，只需可 POST JSON 的端点。
