# Changelog

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

- `docs/PROJECT_STATUS.md`：更新完成度总览
- `docs/roadmap.md`：Phase R 标记完成

---

（较早历史见 git log）
