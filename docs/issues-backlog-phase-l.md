# 可粘贴的 Issue 正文 — Phase L（工程深度与面试叙事）

> 维护者使用：将以下每个 Issue 的 **标题** 和 **正文** 复制到 [GitHub New Issue](https://github.com/xingyun0812/ai-platform-lab/issues/new) 创建。  
> 规划总览见 [phase-l-engineering-depth.md](./phase-l-engineering-depth.md)。  
> **ROI 优先级对照**见 [phase-l-priority-roi.md](./phase-l-priority-roi.md)。

Labels 建议：`phase-task` + `feature` + `phase-l`

Milestone 建议：`Phase L — 工程深度与面试叙事`

---

## 实施状态一览（本地，GitHub Issue 均未创建）

| # | ROI 优先 | 标题 | 文档 | 代码 | 状态 |
|---|----------|------|------|------|------|
| — | 🥇 P1 | **Console 集成跑真** | [phase-l-console-integration.md](./phase-l-console-integration.md) | `console_routes.py` | ✅ **已完成** |
| 62 | 🥇 P1 | Demo 脚本 + 面试手册 | [demo-walkthrough.md](./demo-walkthrough.md) | `eval/platform_demo.sh` | ✅ |
| 63 | 🥇 P1 | SDK 端到端 smoke | [interview-narrative.md](./interview-narrative.md) §SDK | `eval/sdk_smoke.py` | ✅ |
| 53 | P5 并行 | 文档状态对齐 | roadmap/gap/phase-d | — | ✅ |
| 54～57 | 🥈 P2 | RAG 深化 | phase-l §L1 | stub | ⏳ |
| 58～60 | 🥉 P3 | Agent 深化 | phase-l §L2 | 部分 | ⏳ |
| 61 | P4 | 反馈飞轮 E2E | phase-j-feedback-loop | 未 live | ⏳ |

创建顺序建议：`#53`（可并行）→ `#62`/`#63` 补全 → `#54/#55` → … → `#61` → `#62` 面试手册定稿。

---

## #62-console — Console 集成跑真（已完成，可选单独 Issue）

> 若不想单独建 Issue，可将本段合并进 #62 并勾选「Console 子项」。

**标题**：`[Phase L] Console V2 集成跑真 — build + 挂载 + 适配 API`

**状态**：✅ 已完成（见 [phase-l-console-integration.md](./phase-l-console-integration.md)）

### 验收标准（均已满足）

- [x] `npm run build` → `apps/console/static/`
- [x] Gateway 挂载 `/console/` 加载 React（非旧 HTML stub）
- [x] `apps/gateway/console_routes.py` 适配 `/internal/tenants|metrics|settings|rag/*`
- [x] `tests/test_console_routes.py` 通过
- [x] 8 页主要 API 200（Memory/RAG 查询视 env）

---

## #63 — SDK 端到端 smoke

**标题**：`[Phase L] Python SDK 端到端 smoke — chat / rag / agent`

**正文**：

### 目标

`pip install -e sdk/python` 后，对运行中的 Gateway 跑通 chat、rag、agent 三接口；无 Key 时优雅 skip。

### 验收标准

- [ ] `eval/sdk_smoke.py` 可执行，exit 0
- [ ] 文档 [demo-walkthrough.md](./demo-walkthrough.md) 含 SDK 段落
- [ ] `eval/platform_demo.sh` 末尾可选调用 sdk_smoke
- [ ] README Phase L 或 SDK 章节一行说明

### 依赖

- Gateway 运行中
- #62-console ✅（Console 非必须，SDK 独立）

### 预估工期

1～2d

---

## #53 — 文档状态对齐

**标题**：`[Phase L] 文档状态对齐 — roadmap / gap-analysis / 远期规划同步`

**正文**：

### 目标

Phase A～K 已交付，但 `roadmap.md` §已知限制、`gap-analysis-diagram.md`、`phase-d-future-evolution.md` 与代码现状严重矛盾。本 Issue 仅做 **文档同步**，为 Phase L 工程深度 Issue 提供准确基线。

### 验收标准

- [ ] `docs/roadmap.md` — 重写 §已知限制（反映 MCP/HITL/PII/语义缓存/Console 等现状）；新增 Phase L 章节链接
- [ ] `docs/gap-analysis-diagram.md` — Mermaid 节点与完成度表更新至 ~90%
- [ ] `docs/phase-d-future-evolution.md` — 头部标注 D～K 已交付，区分历史规划
- [ ] `docs/PROJECT_STATUS.md` — next steps 指向 Phase L
- [ ] `README.md` — 文档导航增加 Phase L 一行
- [ ] 三份文档对 HITL / MCP / 语义缓存 / Redis Session 描述一致

### 实施计划

**修改文件**（无新 packages）：
- `docs/roadmap.md`
- `docs/gap-analysis-diagram.md`
- `docs/phase-d-future-evolution.md`
- `docs/PROJECT_STATUS.md`
- `README.md`

### 依赖

无

### 预估工期

2d

### 测试计划

1. 人工 diff：搜索「无 MCP」「HITL stub」「无 PII」等过时表述，确保已更新或标注历史
2. 链接检查：Phase L 文档互链可点击

---

## #54 — 真 Rerank Provider

**标题**：`[Phase L] RAG 真 Rerank — API/Local Provider 替换 stub`

**正文**：

### 目标

将 `packages/rag/rerank.py` 从词面 stub 升级为可插拔 **Rerank Provider**（HTTP API 如 Cohere/Jina，或 local cross-encoder 占位），支撑 `eval/run.py compare` 量化 rerank 收益。对标 [enterprise-ai-platform-sop.md](./enterprise-ai-platform-sop.md) RAG 效果深化。

### 验收标准

- [ ] `RerankProvider` 抽象：`stub` | `api` | `local`
- [ ] `config/rag.yaml` + settings：`RAG_RERANK_MODE`、`RAG_RERANK_API_URL`、`RAG_RERANK_MODEL`
- [ ] API Key 走 `packages/secrets/`，不出现在 yaml
- [ ] `/v1/rag/query` 响应 `_platform.rerank_provider` 或 timings 扩展
- [ ] 单测 ≥ 12（mock HTTP，无真实 Key）
- [ ] `docs/phase-l-rerank.md` — stub vs api 对比步骤
- [ ] `eval/acceptance_smoke.py` — rerank provider 切换 smoke
- [ ] `docs/roadmap.md` — #54 ✅

### 实施计划

**新增文件**：
- `packages/rag/rerank_providers.py`（或 `packages/rag/rerank/` 子包）
- `tests/test_rerank_providers.py`
- `docs/phase-l-rerank.md`

**修改文件**：
- `packages/rag/rerank.py` — 委托 provider
- `config/rag.yaml` / `apps/gateway/settings.py` / `.env.example`
- `apps/gateway/rag/query_service.py`（如需暴露 metadata）
- `README.md`

### 依赖

- #53（文档基线，可选）

### 预估工期

1.5w

### 测试计划

1. `python3 tests/test_rerank_providers.py`
2. stub 模式回归：`python eval/acceptance_smoke.py`
3. （可选 live）`eval/run.py compare` stub vs api 报告样例写入 `eval/runs/`

---

## #55 — RAG 增量索引

**标题**：`[Phase L] RAG 增量索引 — chunk 指纹跳过未变内容`

**正文**：

### 目标

索引任务对未变更 chunk 跳过 embed/upsert，降低 re-index 成本；为 kb 版本 bump 后的快速迭代提供工程基础。继承 [phase-d-future-evolution.md](./phase-d-future-evolution.md) §6.1。

### 验收标准

- [ ] chunk 指纹：`content_hash` 或等价字段写入向量 payload / 侧表
- [ ] 二次索引同一 `source_uri`：`skipped_chunks` ≥ 1（metrics 或 task 结果）
- [ ] 修改单段文本：仅受影响 chunk 重新 embed
- [ ] Worker / pipeline 任务结果含 `new_chunks` / `updated_chunks` / `skipped_chunks`
- [ ] 单测 ≥ 10
- [ ] `docs/phase-l-incremental-index.md`
- [ ] `docs/roadmap.md` — #55 ✅

### 实施计划

**新增/修改**：
- `packages/rag/chunker.py` 或 `packages/rag/indexing.py` — 指纹逻辑
- `apps/gateway/rag/pipeline.py` — 增量 upsert
- `packages/tasks/` 或 task 结果 schema
- `tests/test_incremental_index.py`
- `docs/phase-l-incremental-index.md`

### 依赖

无（可与 #54 并行）

### 预估工期

1w

### 测试计划

1. 单测：同内容二次索引 skipped；改一段落仅 1 chunk updated
2. `python eval/acceptance_smoke.py` — index 任务段

---

## #56 — LLM-as-Judge Eval

**标题**：`[Phase L] Eval LLM-as-Judge — 评测从关键词升级为可选 LLM 打分`

**正文**：

### 目标

`eval/pipeline.py` 除 `expected_keywords` 外，支持 `grading: llm_judge`，对 RAG 答案做相关性/ groundedness / 拒答正确性结构化评分；无 `EVAL_API_KEY` 时自动降级 keyword。支撑 RAG 发版对比的可信度。

### 验收标准

- [ ] `eval/pipeline.py` — `CaseGrader`：`keyword` | `llm_judge`
- [ ] JSONL 字段：`grading`、`rubric`（可选）
- [ ] Judge 输出结构化 JSON + 失败降级 keyword
- [ ] 报告 JSON/Markdown 含 `grading_mode` 统计
- [ ] `.github/workflows/eval.yml` — `workflow_dispatch` + secret `EVAL_API_KEY`（可选 job）
- [ ] 单测 ≥ 12（mock LLM）
- [ ] `docs/phase-l-llm-judge-eval.md`
- [ ] `docs/roadmap.md` — #56 ✅

### 实施计划

**新增文件**：
- `eval/graders/keyword.py`、`eval/graders/llm_judge.py`
- `tests/test_llm_judge_grader.py`
- `docs/phase-l-llm-judge-eval.md`

**修改文件**：
- `eval/pipeline.py`、`eval/gate.py`
- `eval/baselines/*.jsonl`（部分用例加 `grading: llm_judge`）
- `.github/workflows/eval.yml`

### 依赖

- #54（rerank 对比实验可选依赖）

### 预估工期

1.5w

### 测试计划

1. 无 Key：全 keyword，pipeline 通过
2. mock Judge：llm_judge 用例 pass/fail 边界
3. `python eval/run.py run-eval` 报告含 grading 字段

---

## #57 — 金丝雀自动回滚 Job

**标题**：`[Phase L] kb 金丝雀自动回滚 — eval 阈值触发 + CLI + 告警`

**正文**：

### 目标

强化 `packages/rag/canary_guard.py`：当最近 eval `pass_rate` 低于阈值时，自动将 `canary_percent` 置 0；提供 CLI、metrics、可选 webhook stub。完成 SOP「version bump + 金丝雀 + eval 对比再全量」的 **自动止血** 闭环。

### 验收标准

- [ ] CLI：`python -m packages.rag.canary_guard check --kb-id lab-demo --min-pass-rate 0.85`
- [ ] 写回 `data/canary_guard.json` + 可选 `data/tenant_overrides.json` 或 rag routing override
- [ ] Prometheus 指标：`canary_auto_rollback_total`
- [ ] Webhook stub：`CANARY_GUARD_WEBHOOK_URL` opt-in
- [ ] 单测 ≥ 10（模拟 pass_rate 高/低）
- [ ] `docs/phase-l-canary-guard.md` — 与 #56 eval 报告联动说明
- [ ] `docs/roadmap.md` — #57 ✅

### 实施计划

**修改/新增**：
- `packages/rag/canary_guard.py` — CLI + webhook
- `apps/gateway/rag/pipeline.py` — 集成点确认
- `tests/test_canary_guard.py`
- `docs/phase-l-canary-guard.md`
- `eval/acceptance_smoke.py` — canary guard 段

### 依赖

- #56（eval pass_rate 来源标准化）

### 预估工期

1w

### 测试计划

1. 单测：pass_rate 0.7 → rollback；0.9 → no-op
2. CLI 手动跑通 + 读 routing API 验证 canary_percent=0

---

## #58 — Agent 三率指标

**标题**：`[Phase L] Agent 三率指标 — Needless / Missing / Arg Valid + Precision@1`

**正文**：

### 目标

扩展 `eval/agent_run.py`，输出大厂 Agent 效果指标，对标 [enterprise-ai-platform-sop.md](./enterprise-ai-platform-sop.md) §Agent L2。

### 验收标准

- [ ] 指标：Tool Precision@1、Needless Tool Rate、Missing Tool Rate、Arg Valid Rate
- [ ] `eval/baselines/agent_scenarios.jsonl` 字段：`direct_answer`、`require_tools`、`expect_tools`、`forbid_tools`
- [ ] `eval/agent_run.py run` JSON 报告含 `agent_metrics` 块
- [ ] `eval/agent_run.py compare` 对比四率变化
- [ ] 单测 ≥ 10
- [ ] `docs/phase-l-agent-metrics.md`
- [ ] `docs/roadmap.md` — #58 ✅

### 实施计划

**修改文件**：
- `eval/agent_run.py`
- `eval/baselines/agent_scenarios.jsonl`
- `tests/test_agent_metrics.py`
- `docs/phase-l-agent-metrics.md`

### 依赖

- #57 完成或并行（无硬依赖）

### 预估工期

1w

### 测试计划

1. 构造 4 类用例，断言各率计算正确
2. `python eval/agent_run.py validate-baseline`

---

## #59 — Agent Vertical 端到端用例

**标题**：`[Phase L] Agent Vertical — Orchestrator + Multi-Agent + HITL 串联演示`

**正文**：

### 目标

提供一条可复现的 **端到端 Agent 治理故事**：工作流编排 → 多 Agent 委托 → 高风险工具 HITL 审批 → 审计落库。写入演示文档与 smoke。

### 验收标准

- [ ] 场景文档 `docs/demo-agent-vertical.md` — curl / SDK 逐步命令
- [ ] 预置 AgentSpec：`rag_specialist` + workflow 定义（YAML 或 API 脚本）
- [ ] `eval/baselines/agent_scenarios.jsonl` — `vertical-hitl-01`
- [ ] `eval/acceptance_smoke.py --agent-vertical`（无 Key 测 HITL 状态机；有 Key 全链路）
- [ ] 审计记录含 `action_level` + HITL `approval_id`
- [ ] 单测 ≥ 8（mock agent run + hitl approve）
- [ ] `docs/roadmap.md` — #59 ✅

### 实施计划

**新增/修改**：
- `docs/demo-agent-vertical.md`
- `config/agent_vertical.yaml` 或 `scripts/seed_agent_vertical.py`
- `eval/acceptance_smoke.py`
- `tests/test_agent_vertical.py`

### 依赖

- #58

### 预估工期

1.5w

### 测试计划

1. 无 LLM：`acceptance_smoke.py --agent-vertical` HITL 状态机
2. 有 LLM：文档命令人工 walkthrough 勾选

---

## #60 — Agent Baseline 扩充 + CI 门禁

**标题**：`[Phase L] Agent Baseline 扩充 + PR 评测门禁`

**正文**：

### 目标

`agent_scenarios.jsonl` 扩充至 ≥ 30 条；PR CI 跑 agent eval gate（相对 main 回退 >5% block），与 RAG gate 对称。

### 验收标准

- [ ] `eval/baselines/agent_scenarios.jsonl` ≥ 30 条，覆盖三率场景
- [ ] `eval/agent_gate.py` 或扩展 `eval/gate.py`
- [ ] `.github/workflows/eval.yml` — agent job
- [ ] `eval/baselines/main_baseline.json` 更新 agent 基线
- [ ] 单测 ≥ 8
- [ ] `docs/phase-l-agent-eval-gate.md`
- [ ] `docs/roadmap.md` — #60 ✅

### 实施计划

**新增/修改**：
- `eval/baselines/agent_scenarios.jsonl`
- `eval/agent_gate.py`
- `.github/workflows/eval.yml`
- `tests/test_agent_gate.py`
- `docs/phase-l-agent-eval-gate.md`

### 依赖

- #58（指标定义）

### 预估工期

1w

### 测试计划

1. gate 单测：回退 6% → block；提升 2% → pass
2. `python eval/agent_run.py run --min-pass-rate 0.7`（mock/live）

---

## #61 — 反馈飞轮 LLM 实测闭环

**标题**：`[Phase L] 反馈飞轮 E2E — Bad Case → Eval → Prompt 建议 → A/B`

**正文**：

### 目标

跑通 `packages/feedback_loop/` 真实闭环（mock 单测 + live 文档），连接反馈 API、bad_cases、Prompt 实验。

### 验收标准

- [ ] `docs/phase-l-feedback-loop-e2e.md` — live 命令清单
- [ ] `eval/feedback_loop_demo.py` — `--mock` / `--live`
- [ ] mock 单测 ≥ 10 覆盖 `run_full_cycle`
- [ ] live：点踩 → bad_cases.jsonl → PromptSuggestion → experiment 创建（需 Key）
- [ ] `docs/roadmap.md` — #61 ✅

### 实施计划

**新增/修改**：
- `eval/feedback_loop_demo.py`
- `docs/phase-l-feedback-loop-e2e.md`
- `tests/test_feedback_loop_e2e.py`
- 必要时小改 `packages/feedback_loop/pipeline.py`

### 依赖

- #56（eval 入库格式）
- #60（agent/rag gate 基线，软依赖）

### 预估工期

1w

### 测试计划

1. `python eval/feedback_loop_demo.py --mock`
2. live walkthrough 记入文档 checklist

---

## #62 — 平台 Demo + 面试叙事手册

**标题**：`[Phase L] 平台 Demo 脚本 + 面试叙事手册`

**正文**：

### 目标

交付 **15 分钟可演示** 与 **10 分钟可背诵** 的面试材料。Console 集成见 **#62-console ✅**。

### 验收标准

- [x] Console build + 挂载（#62-console，已完成）
- [ ] `docs/demo-walkthrough.md` — 全链路步骤与话术（已建骨架，需 live 勾选）
- [ ] `docs/interview-narrative.md` — 10 分钟口述 + Q&A + 诚实边界
- [ ] `eval/platform_demo.sh` — `--no-llm` / `--with-llm` 全绿
- [ ] `acceptance_smoke.py` 可选 `--platform-demo` 段
- [ ] `docs/roadmap.md` — #62 ✅

### 依赖

- #62-console ✅
- 软依赖 #57（RAG 回滚故事）、#59（Agent vertical）— 无则 Demo 用 curl 占位
- [ ] `docs/interview-narrative.md` — 6 层架构 + 2 深度故事 + Q&A + 诚实边界
- [ ] `eval/platform_demo.sh` — `--no-llm` / `--with-llm`
- [ ] `console-v2`：`npm run build` 产出至 `apps/console/static/`，README 启动说明
- [ ] `acceptance_smoke.py` — `--platform-demo` 段
- [ ] `docs/roadmap.md` — Phase L 全部 ✅，打 tag 说明 `phase-l-engineering-depth`
- [ ] `docs/phase-l-engineering-depth.md` — 头部状态改为已完成

### 实施计划

**新增文件**：
- `docs/demo-walkthrough.md`
- `docs/interview-narrative.md`
- `eval/platform_demo.sh`

**修改文件**：
- `console-v2/` build 配置 → `apps/console/static/`
- `apps/gateway/main.py` 或 console routes
- `README.md`
- `eval/acceptance_smoke.py`

### 依赖

- #57（RAG 发版故事）
- #59（Agent vertical 故事）
- #61（反馈飞轮，可选段落）

### 预估工期

1w

### 测试计划

1. `./eval/platform_demo.sh --no-llm`  exit 0
2. 按 `interview-narrative.md` 自检清单勾选

---

## 创建后的维护动作

1. **Labels**：每个 Issue 加 `phase-task` + `feature` + `phase-l`
2. **Milestone**：`Phase L — 工程深度与面试叙事`
3. **依赖评论**：
   - `#56 depends on #54`
   - `#57 depends on #56`
   - `#59 depends on #58`
   - `#60 depends on #58`
   - `#61 depends on #56, #60`
   - `#62 depends on #57, #59, #61`
4. **合并完成后**：打 tag `phase-l-engineering-depth`
5. **更新** [phase-l-engineering-depth.md](./phase-l-engineering-depth.md) 头部状态
