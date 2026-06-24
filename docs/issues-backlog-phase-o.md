# Phase O Issue Backlog — Agent 能力对齐 JD2

> 规划：[phase-o-agent-jd2-alignment.md](./phase-o-agent-jd2-alignment.md)  
> **Milestone**：Phase O — Agent JD2 Alignment  
> **Tag**：`phase-o-agent-jd2`  
> **前置**：Phase N（PyPI SDK）收尾

| Backlog | GitHub Issue | 状态 |
|---------|--------------|------|
| 规划文档 | [#86](https://github.com/xingyun0812/ai-platform-lab/issues/86) | ⏳ |
| O1 Task Planner | [#87](https://github.com/xingyun0812/ai-platform-lab/issues/87) | ✅ #99 |
| O2 CoT 推理 | [#88](https://github.com/xingyun0812/ai-platform-lab/issues/88) | ✅ #100 |
| O4 Multi-Agent v2 | [#89](https://github.com/xingyun0812/ai-platform-lab/issues/89) | ✅ #101 |
| O5 Plugin Manifest | [#90](https://github.com/xingyun0812/ai-platform-lab/issues/90) | ✅ #102 |
| O6 web_search | [#91](https://github.com/xingyun0812/ai-platform-lab/issues/91) | ✅ #103 |
| O7 sql_query | [#92](https://github.com/xingyun0812/ai-platform-lab/issues/92) | ✅ #104 |
| O9 数据分析 Vertical | [#93](https://github.com/xingyun0812/ai-platform-lab/issues/93) | ⏳ |
| O10 Agent 性能 | [#94](https://github.com/xingyun0812/ai-platform-lab/issues/94) | ⏳ |
| O11 文档 Demo 门禁 | [#95](https://github.com/xingyun0812/ai-platform-lab/issues/95) | ⏳ |

---

## 规划 Issue — Phase O 总览

**标题**：`[Phase O] Agent 能力对齐 JD2 智能体研发岗 — 规划`

**目标**：对照 JD2 §4.1，补齐 Planner / CoT / Multi-Agent v2 / 外部工具 / 数据分析 vertical / 性能与叙事。

**验收**：
- [ ] `docs/phase-o-agent-jd2-alignment.md` 评审通过
- [ ] Milestone 内 O1～O11 Issue 创建完毕
- [ ] `roadmap.md` 增加 Phase O 章节

**非目标**：RPA、PyTorch、LangChain 依赖

**预估**：4～5 周（按 Wave 串行 merge）

---

## O1 — Task Planner + 任务分解

**标题**：`[Phase O] O1 Task Planner — LLM 结构化规划与逐步执行`

**JD 对齐**：任务规划、自动化任务分解

**目标**：用户给出 `goal`，平台生成 `Plan`（多步 + 依赖），并驱动 Agent Runner 逐步执行。

**验收**：
- [ ] `packages/agent/planner.py` + `Plan` / `PlanStep` schema
- [ ] `POST /v1/agent/plan` 或 `auto_plan=true` on `/v1/agent/run`
- [ ] Prompt `agent_planner` in `config/prompts.yaml`
- [ ] 单测 ≥12（`tests/test_agent_planner.py`）
- [ ] `eval/agent_planner_smoke.py` mock 通过

**关键文件**：
- `packages/agent/planner.py`
- `packages/contracts/schemas.py`
- `apps/gateway/platform_routes.py`
- `config/prompts.yaml`

**依赖**：无

**分支**：`feat/issue-<N>-agent-planner`

**预估工期**：3～4d

---

## O2 — CoT 推理模式

**标题**：`[Phase O] O2 CoT reasoning mode — 显式链式推理 trace`

**JD 对齐**：链式推理（CoT）

**目标**：`AGENT_REASONING_MODE=cot` 时解析 `<thinking>` 块并写入 `tool_trace`，默认 `react` 不变。

**验收**：
- [x] `packages/agent/reasoning.py` 解析器
- [x] `runner.py` 集成 + `config/agent.yaml`
- [x] 单测 mock LLM 输出
- [x] `eval/agent_run.py` baseline 增 cot 用例（mock）

**依赖**：无（可与 O1 并行开发，merge 建议同 Wave）

**预估工期**：2d

---

## O4 — Multi-Agent v2（黑板 + Runner 委托）

**标题**：`[Phase O] O4 Multi-Agent v2 — shared blackboard + full Runner delegation`

**JD 对齐**：Multi-Agent 协作

**目标**：委托子 Agent 走完整 `run_agent()`；Redis 黑板；可选 reviewer 流程；Console 可查黑板。

**验收**：
- [x] `packages/agent/multi_agent/blackboard.py`
- [x] `delegation.py` 改调 Runner
- [x] `GET /v1/agent/blackboard/{session_id}`
- [x] 单测 + 扩展 `agent_vertical_smoke`
- [x] 更新 `docs/phase-h-multi-agent.md` 边界说明

**依赖**：O1 可选

**预估工期**：4～5d

---

## O5 — Plugin Manifest 动态工具

**标题**：`[Phase O] O5 Plugin manifest — YAML 动态注册工具`

**JD 对齐**：插件系统

**目标**：`config/plugins/*.yaml` 声明工具，启动加载到 registry；租户 ACL 不变。

**验收**：
- [x] `packages/agent/plugins/loader.py`
- [x] 示例 `config/plugins/demo_echo.yaml`
- [x] 单测 ≥10
- [x] 插件作者 mini 文档

**依赖**：无

**预估工期**：2～3d

---

## O6 — web_search 工具

**标题**：`[Phase O] O6 web_search tool — mock + HTTP 外部检索`

**JD 对齐**：搜索引擎集成

**目标**：Agent 工具 `web_search`；`WEB_SEARCH_MODE=mock|http`；返回结构化 top-k。

**验收**：
- [x] `packages/agent/tools/web_search.py`
- [x] mock 单测 + agent 集成测
- [x] `.env.example` 注释

**依赖**：O5 可选

**预估工期**：2d

---

## O7 — sql_query 只读工具

**标题**：`[Phase O] O7 sql_query tool — 只读 SQL 沙箱`

**JD 对齐**：数据库工具集成

**目标**：仅允许 SELECT；强制 LIMIT；拒绝 DML/DDL；只读连接 URL。

**验收**：
- [x] `packages/agent/tools/sql_query.py`
- [x] SQL 解析拒绝写操作
- [x] `samples/analytics_demo.sql` seed
- [x] 单测 ≥10

**依赖**：沙箱 #41 已有；可与 O6 同 Wave

**预估工期**：2～3d

---

## O9 — 数据分析 Vertical

**标题**：`[Phase O] O9 Data analysis vertical — 搜索 + SQL + calc 演示链`

**JD 对齐**：办公/数据分析业务场景

**目标**：一条可演示链路：web_search（mock）→ sql_query → calc → 报告摘要。

**验收**：
- [ ] `config/workflows/data_analysis.yaml` 或 agent spec
- [ ] `eval/data_analysis_vertical.sh --mock` exit 0
- [ ] `--live` 文档说明
- [ ] Console 轨迹可展示 plan/blackboard

**依赖**：O1、O6、O7（O4 可选）

**预估工期**：3～4d

---

## O10 — Agent 性能调优

**标题**：`[Phase O] O10 Agent perf — 并行工具 + metrics + 长上下文策略`

**JD 对齐**：性能调优

**目标**：`tool_call_strategy`；并行 tool asyncio；Prometheus 新指标。

**验收**：
- [ ] 并行/顺序单测
- [ ] `/metrics` 新 counter/histogram
- [ ] 长上下文策略文档化

**依赖**：无

**预估工期**：2～3d

---

## O11 — 文档 / Demo / eval 门禁

**标题**：`[Phase O] O11 Docs demo gate — 叙事同步 + agent_jd2_gate + tag`

**目标**：更新 narrative / demo / JD 对照；CI gate；打 tag。

**验收**：
- [ ] `eval/agent_jd2_gate.py` + CI
- [ ] `interview-narrative.md` / `demo-walkthrough.md` 更新
- [ ] `tmp-jd-platform-comparison.md` §4.1 评级更新
- [ ] Tag `phase-o-agent-jd2`

**依赖**：O1～O10

**预估工期**：2d

---

## JD2 §4.1 完成后预期评级

| JD 要点 | 当前 | Phase O 后 |
|---------|------|------------|
| 任务规划 | ⚠️ | ✅ |
| 自动化任务分解 | ⚠️ | ✅ |
| CoT | ⚠️ | ✅ |
| Multi-Agent | ⚠️ | ✅ |
| 插件系统 | ⚠️ | ✅ |
| 搜索引擎 | ⚠️ | ✅ |
| 数据库 | ⚠️ | ✅ |
| 办公/数据分析 | ⚠️ | ✅ |
| 性能调优 | ⚠️ | ✅ |
| RPA | ❌ | ❌（刻意不做） |
