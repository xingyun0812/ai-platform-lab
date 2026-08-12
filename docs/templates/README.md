# Agent 架构模板库

> **快速搭建指南** — 面向新加入的开发者或面试场景

## 模式对比

| 模式 | 文件 | API | 核心组件 | 适用场景 |
|------|------|-----|----------|----------|
| **ReAct** | `packages/agent/react_loop.py` | `POST /v1/agent/run` | `ReActLoop` + `ToolRegistry` | 简单工具调用、问答 |
| **ToT** | `packages/agent/tot/` | `POST /v1/agent/tot` | `ThoughtTree` + `TotSearcher` | 复杂推理、数学题 |
| **Debate** | `packages/agent/debate/` | `POST /v1/agent/debate` | `parallel_delegate` + Judge | 事实性问题的多视角验证 |
| **Research** | `packages/agent/research/` | `POST /v1/agent/research` | `QuestionDecomposer` + `ResearchSearcher` | 开放域研究、信息搜集 |
| **Computer Use** | `packages/agent/computer_use/` | `POST /v1/agent/computer-use` | `ComputerUsePlanner` + `ComputerUseExecutor` | GUI 操作、浏览器自动化 |

## 选择指南

```
需要做什么？
├─ 调用工具/API → ReAct
├─ 复杂推理（数学/逻辑） → ToT
├─ 事实性问题的交叉验证 → Debate
├─ 自主搜索和综合信息 → Research
└─ 操作 GUI 界面 → Computer Use
```

## 快速开始

每个模板包含：
1. 架构图（ASCII）
2. 核心流程
3. 代码骨架
4. curl 调用示例
5. 关键配置
6. 与其它模式的关系
