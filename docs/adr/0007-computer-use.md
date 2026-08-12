# ADR-0007: Computer Use Agent

- **Status**: accepted
- **Date**: 2026-08-06
- **Tags**: phase-v, agent, computer-use

## Context

项目已交付 ReAct、ToT、Debate、Deep Research，但所有 Agent 都只能调用 API 工具。Computer Use 填补 GUI 操作的空白。

## Decision

### 1. 截图→分析→动作循环

使用循环架构：每次迭代先截图，LLM 分析后决定下一步动作，执行后验证。

### 2. 纯 Python 库方案

优先 `mss`（截图）和 `pyautogui`（输入），不依赖 Docker Xvfb。import 失败时降级为 mock。

### 3. 坐标归一化

LLM 返回 0-1000 归一化坐标，执行器映射到实际分辨率。

### 4. API 独立端点

`POST /v1/agent/computer-use`，独立于现有的 Agent run 端点。

## Consequences

### Positive

- Agent 能力从 API 扩展到 GUI
- 降级链确保在无头环境也能运行
- 复用现有编排模式

### Negative

- 需要 mss/pyautogui/PIL 等依赖（可选）
- 无头服务器需 Xvfb 或 Docker
- LLM 视图中截图识别准确性受限于模型的多模态能力

## References

- `packages/agent/computer_use/__init__.py`
- Anthropic Computer Use reference implementation
