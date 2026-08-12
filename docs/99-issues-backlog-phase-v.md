# 高级推理后续 Issue — Phase V/W/X 规划

> **说明**：Phase S（ToT）、Phase T（Debate）、Phase U（Deep Research）已交付。以下为后续可实施的方向，按优先级排列。

---

## #V1 — Computer Use Agent

**标题**：`[Phase V] Computer Use Agent — GUI 操作能力`

### 目标

让 Agent 能通过「截图 → LLM 分析 → 定位 → 点击/输入」的方式操作 GUI 界面。复用现有 `packages/sandbox/` Docker 沙箱隔离执行环境。

### 验收标准

- [ ] `packages/agent/tools/computer_use.py` — 核心工具：`screenshot`、`click`、`type`、`move`、`scroll`、`key`
- [ ] Docker 沙箱执行（`SANDBOX_ENABLED`）与直接执行（`process` 模式）双模式
- [ ] `POST /v1/agent/computer-use` 独立 API 端点
- [ ] 单测 ≥ 10 个（mock 截图和输入）
- [ ] 设计文档 `docs/02-phase-v-computer-use.md`
- [ ] ADR `docs/adr/0007-computer-use.md`

### 新增文件

| 文件 | 职责 |
|------|------|
| `packages/agent/tools/computer_use.py` | 截图、鼠标、键盘操作工具 |
| `packages/agent/computer_use/__init__.py` | `run_computer_use()` 编排器 |
| `packages/agent/computer_use/models.py` | `ComputerUseConfig`, `ComputerUseResult` |
| `packages/agent/computer_use/planner.py` | 屏幕分析 → 动作规划 |
| `apps/gateway/agent/computer_use_routes.py` | `POST /v1/agent/computer-use` |
| `tests/test_computer_use.py` | 单元测试 |
| `docs/02-phase-v-computer-use.md` | 设计文档 |
| `docs/adr/0007-computer-use.md` | 架构决策 |

### 修改文件

| 文件 | 修改 |
|------|------|
| `packages/agent/registry.py` | 注册 `computer_use` 工具 |
| `apps/gateway/router_registry.py` | 注册新路由 |
| `docs/00-roadmap.md` | 更新 |
| `docs/00-PROJECT_STATUS.md` | 更新 |

### 设计要点

1. **截图**：`import pyautogui` 或 Docker 内 `Xvfb + scrot`
2. **坐标定位**：LLM 返回点击坐标（归一化 0-1 或绝对像素）
3. **安全隔离**：默认仅在 Docker 沙箱内执行，`process` 模式需显式启用
4. **动作序列**：支持多步规划（先截图 → 分析 → 点击 → 再截图 → 验证）

### 预估工期

2-3 天

---

## #V2 — ToT MCTS 搜索扩展

**标题**：`[Phase V] ToT MCTS 搜索算法扩展`

### 目标

在现有 ToT BFS/DFS 基础上增加 MCTS（Monte Carlo Tree Search）算法。`ThoughtNode.visits` 字段已预留。

### 验收标准

- [ ] `TotSearcher._search_mcts()` — UCB 计算 + 选择→扩展→模拟→回溯
- [ ] `config/agent_tot.yaml` 新增 `search_algorithm: "mcts"`
- [ ] MCTS 特定配置：`mcts_exploration_weight`, `mcts_simulations`
- [ ] 单测覆盖 MCTS 搜索路径
- [ ] 更新设计文档

### 新增/修改文件

| 文件 | 修改 |
|------|------|
| `packages/agent/tot/searcher.py` | 新增 `_search_mcts()` |
| `packages/agent/tot/tree.py` | 扩展 `TotConfig`（新增 MCTS 参数） |
| `config/agent_tot.yaml` | 新增 MCTS 配置 |
| `tests/test_tot.py` | 新增 MCTS 测试 |

### 预估工期

1 天

---

## #V3 — Deep Research 迭代深入

**标题**：`[Phase V] Deep Research 迭代深入 — 信息缺口识别与补充搜索`

### 目标

当前 Research 只有单轮搜索+综合。增加迭代深入：综合后 LLM 识别信息缺口，生成补充查询，第二轮搜索。

### 验收标准

- [ ] `ResearchSynthesizer.identify_gaps()` — 分析报告识别信息缺口
- [ ] `ResearchGapFiller` — 基于缺口生成补充搜索查询
- [ ] `max_depth=2` 时执行两轮搜索
- [ ] 报告标注已覆盖和未覆盖的主题
- [ ] 单测覆盖迭代流程

### 新增/修改文件

| 文件 | 修改 |
|------|------|
| `packages/agent/research/synthesizer.py` | 新增 `identify_gaps()` |
| `packages/agent/research/gap_filler.py` | 新增：信息缺口补充搜索 |
| `packages/agent/research/__init__.py` | 扩展 `run_research()` 迭代逻辑 |
| `tests/test_research.py` | 新增迭代测试 |

### 预估工期

1 天

---

## #V4 — Computer Use + Deep Research 融合

**标题**：`[Phase V] 多模态 Deep Research — 结合 Computer Use 截图分析`

### 目标

Deep Research 阅读网页时，不仅抓取文本，还对网页截图进行多模态分析（图表、图片、可视化数据）。

### 验收标准

- [ ] `ResearchSearcher` 中对关键结果截图
- [ ] 截图发送到多模态 LLM 分析 → 提取图表数据
- [ ] 截图分析结果纳入 ResearchNote
- [ ] 单测覆盖截图分析流程

### 依赖

#V1 Computer Use Agent（截图能力）

### 预估工期

1 天

---

## #V5 — OPA 策略引擎集成

**标题**：`[Phase V] OPA 策略引擎 — 企业级策略管控`

### 目标

集成 Open Policy Agent (OPA)，将现有的代码级 ACL 策略迁移到 Rego 策略文件，支持运行时策略热加载。

### 验收标准

- [ ] `packages/opa/` — Rego 策略加载 + 评估引擎
- [ ] `config/policies/` — 示例策略：租户隔离、工具权限、数据脱敏
- [ ] 网关 middleware 集成策略检查点
- [ ] 单测覆盖策略评估
- [ ] 设计文档

### 预估工期

2-3 天

---

## #V6 — Agent 模板库

**标题**：`[Phase V] Agent 架构模板库 — 快速搭建指南`

### 目标

整理项目中的 Agent 模式为可复用的模板文档，方便快速搭建各类 Agent。

### 验收标准

- [ ] `docs/templates/` 目录
- [ ] ReAct Agent 模板
- [ ] ToT Agent 模板
- [ ] Multi-Agent Debate 模板
- [ ] Deep Research 模板
- [ ] Plan-and-Execute 模板

### 预估工期

1 天

---

## 优先级建议

| 优先级 | Issue | 面试价值 | 工时 |
|--------|-------|----------|------|
| P0 | #V1 Computer Use Agent | ⭐⭐⭐⭐⭐ | 2-3 天 |
| P1 | #V2 ToT MCTS 扩展 | ⭐⭐⭐⭐ | 1 天 |
| P1 | #V3 Research 迭代深入 | ⭐⭐⭐ | 1 天 |
| P2 | #V4 多模态 Research（依赖 V1） | ⭐⭐⭐⭐⭐ | 1 天 |
| P2 | #V5 OPA 集成 | ⭐⭐⭐ | 2-3 天 |
| P3 | #V6 模板库 | ⭐⭐ | 1 天 |
