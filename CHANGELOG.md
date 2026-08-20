# Changelog

## 2026-08-20 — Phase Y Agent Production Guardrails (#225-#228)

### Added

- `packages/agent/guardrails/` — 新子包，四层防护共享基础设施
  - `AgentGuardrailConfig` — 统一配置 dataclass（四层参数 + 全局开关）
  - `check_convergence()` — 收敛检测共享组件（从 Self-Refine 抽取，3 策略）
  - `ProgressTracker` — 滑动窗口 stuck 检测（连续空结果/同参数重复/输出循环）
  - `ThresholdEnforcer` — 熔断计数器（全局上限 + 按工具类型 + 超时）
- **Layer 1 — 粒度硬约束**：`planner.validate_plan()` 校验 step 数上限和最小执行单元
- **Layer 2 — 收敛检测**：`react_loop.py` 中，连续无 tool_call 且输出稳定时提前终止
- **Layer 3 — 进度校验**：`plan_executor.py` + `react_loop.py` 接入 stuck 检测
- **Layer 4 — 熔断告警**：settings.py 新增 8 个 `AGENT_GUARDRAIL_*` env var，触发熔断时记录 Prometheus 指标
- **Self-Refine** — 收敛检测改为调用 `guardrails.convergence` 共享组件，向后兼容
- **Prometheus 指标**：`guardrail_triggered_total{layer,reason}` + `guardrail_stuck_total{reason}`

### Tests

- 23 个 guardrails 单元测试（全部不依赖 LLM）
- 127 个回归测试全部通过（guardrails + self_refine + memory）

---

## 2026-08-19 — Phase X.5 Memory Classification (#222)

### Added

- `packages/memory/classifier/` — L0 记忆分类器模块（规则 + LLM 双轨）
- `ClassResult` dataclass + `run_classifier()` 编排函数
- 规则分类器：关键词/模式匹配零依赖拦截噪音
- LLM 分类器：200ms 超时降级，复用 verify.py LLM 调用
- 集成到 MemoryStore.add()：L1 之后插入，影响 scope/TTL/权重
- `MemoryGovernanceConfig` 扩展：classifier 开关、模型、超时、fallback
- 6 个分类器 Prometheus 指标（classified/latency/llm_calls/llm_errors/rule_matched）
- `PATCH /{memory_id}/classify` 管理端点（platform_admin 手动纠正分类）

### Tests

- 38 个分类器测试（规则 12 + LLM 10 + 集成 8 + API 8）
- 75 个 Phase X 回归测试全部通过

---

## 2026-07-07 — Sprint 1~3：Harness 工程最佳实践

### Added

- `CLAUDE.md`：Harness 核心 Project Instructions
- `.claude/settings.json`：项目级权限配置
- `.claude/launch.json`：gateway/console-v2/worker 一键启动
- `.claude/agents/code-review.md`、`eval-gate.md`：专用 Agent 定义
- `.claude/workflows/ci-monitor.md`：CI 状态轮询 Workflow
- `.pre-commit-config.yaml`：ruff + baseline 校验
- `Justfile`：16 个命令别名
- `scripts/setup-hooks.sh`：pre-commit 安装脚本
- `.cursor/rules/agent-module-style.mdc`：完善 Python 模块风格规则（154 行）

### Changed

- `pyproject.toml`：+mypy 配置（packages/platform/ + contracts/ 启用）、dev deps 升级
- `.claude/settings.local.json`：精简为仅 git 权限

### Known Gaps

- just 需要 `brew install just` 独立安装
- mypy 仅覆盖 platform/ + contracts/，其余模块暂忽略
- 无安全扫描（Dependabot/Trivy/Safety）

---

## 2026-06-29 — Phase R Agent Harness (#137)

### Added

- `packages/agent/self_evolve.py`：Agent 自进化主循环（reflect → patch → HITL）
- `packages/agent/long_horizon.py`：长程任务 checkpoint/resume
- `packages/agent/capability_profile.py`：四维模型能力画像
- `eval/harness_capability_gate.py`：7 项离线 CI 门禁
- `eval/harness_capability_benchmark.py`：4 维 benchmark mock
- `apps/gateway/harness_routes.py`：Harness REST API

### Changed

- `docs/00-PROJECT_STATUS.md`：更新完成度总览
- `docs/00-roadmap.md`：Phase R 标记完成

---

（较早历史见 git log）
