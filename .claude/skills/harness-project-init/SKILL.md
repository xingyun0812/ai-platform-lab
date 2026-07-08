---
name: harness-project-init
model: fable
model_config:
  skills:
    # 依赖的系统级 skill
    - update-config
    - claude-api
description: >
  用 ai-platform-lab 的 Harness 工程最佳实践来初始化一个新项目。
  创建 CLAUDE.md、.claude/settings.json、launch.json、pre-commit hooks、
  Justfile、Agents、Workflows、Memory 系统，一条命令搭好工程底座。
  适合任何 Python/TypeScript 项目，新项目或是已有项目都可以。
---

# Harness Project Init

用 `ai-platform-lab` 沉淀的 Harness 工程最佳实践初始化一个新项目。

---

## 用法

在目标项目根目录执行：

```bash
# 交互式问答引导
/skill harness-project-init

# 指定项目类型（跳过问答）
/skill harness-project-init python-311
/skill harness-project-init fastapi-react
/skill harness-project-init cli-tool
```

---

## Workflow

### Step 1：了解项目信息

先问用户几个问题，不要猜：

- 项目类型：Python CLI / FastAPI 后端 / FastAPI + React 全栈 / TypeScript 库 / 纯前端
- Python 版本（如果适用）
- 主要依赖和技术栈（FastAPI、Django、Express、React、Vite 等）
- 启动命令（docker compose / uvicorn / npm run dev / cargo run 等）
- 测试命令（pytest / vitest / cargo test 等）
- 是否已有项目还是全新的

如果用户通过参数指定了类型（如 `python-311`），跳过相关问答，使用默认值。

### Step 2：创建 Harness 基础文件

按以下顺序创建文件。每个文件创建后简短说明它的用途。

#### 2.1 CLAUDE.md

```markdown
# <project-name> — Project Instructions

## Role
<项目一句话定位，如：一个高性能 Redis 协议网关>

## Build & Run

```bash
# 启动方式
<docker compose up -d --build 或 uvicorn xxx 或 cargo run>

# 健康检查
<curl http://localhost:PORT/healthz>
```

## Test

```bash
# 单元测试
<python -m pytest tests/ -q 或 npm test 或 cargo test>

# 带覆盖率
<python -m pytest tests/ --cov-report=term-missing -q 或 vitest run --coverage>
```

## Code Standards

- **语言**: Python 3.11+，`from __future__ import annotations` in all new files
- **Style**: ruff (line-length=100, select E/F/I/UP)。Run `ruff check . && ruff format --check .`
- **Process**: Issue → feature branch → PR (with acceptance alignment) → merge
- **No direct pushes to main**

## Config & Deploy

| Purpose | Path |
|---------|------|
| 配置文件 | `config/*.yaml` / `config/*.json` |
| Docker Compose | `docker-compose.yml` |
| Dockerfile | `Dockerfile` |
| K8s manifests | `deploy/k8s/` |

## Environment

| Variable | Purpose |
|----------|---------|
| `<ENV_VAR>` | 请补充 |
```

模板要点：
- 保持 60-80 行精炼，只放不可从代码推断的信息
- 构建/测试命令要实际可执行（执行一次验证）
- 去掉 Python 特有的东西（如 ruff），如果项目是 TypeScript/Go/Rust

#### 2.2 .claude/settings.json

```json
{
  "project": {
    "description": "<短描述>",
    "githubRepo": "<user>/<repo>"
  },
  "permissions": {
    "allow": [
      "Bash(<项目常用命令匹配模式>)"
    ]
  }
}
```

权限列表根据实际命令推导：
- Python 项目：`Bash(uvicorn *)`, `Bash(python -m pytest *)`, `Bash(ruff *)`, `Bash(docker compose *)`
- Node 项目：`Bash(npm *)`, `Bash(npx *)`, `Bash(vitest *)`
- Rust 项目：`Bash(cargo *)`

#### 2.3 .claude/launch.json

```json
{
  "version": "0.0.1",
  "configurations": [
    {
      "name": "<server-name>",
      "runtimeExecutable": "<可执行文件>",
      "runtimeArgs": ["<参数>"]
    }
  ]
}
```

根据项目实际启动方式创建。全栈项目可以有多个 server（后端 + 前端）。

#### 2.4 .pre-commit-config.yaml（仅 Python 项目）

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.11.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: local
    hooks:
      - id: check-added-large-files
        name: check added large files
        entry: check-added-large-files
        language: system
        args: ['--maxkb=500']
```

非 Python 项目使用相应的 linter/formatter 镜像。

#### 2.5 scripts/setup-hooks.sh

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "==> 安装 pre-commit..."
if ! command -v pre-commit &>/dev/null; then
    pip install pre-commit
fi
pre-commit install
echo "==> pre-commit hooks 已安装"
```

非 Python 项目用 husky 或 lefthook 替代。

#### 2.6 Justfile（可选，仅用户确认使用 just 时创建）

```makefile
# <project-name> Justfile

# 启动
up:
    <启动命令>

# 测试
test:
    <测试命令>

# Lint
lint:
    <lint 命令>

# 列出所有命令
_default:
    @just --list
```

### Step 3：创建 Agents & Workflows

如果项目是 Python 项目或复杂度 2+，创建以下文件：

#### 3.1 .claude/agents/code-review.md

```markdown
# code-review

**Agent**: Python 代码审查
**Trigger**: `/agent code-review`

## Role

你是一个资深代码审查工程师。审查 PR 中的修改文件，发现 linter 无法自动检测的问题。

## 审查维度

1. **并发安全** — Lock 使用是否正确？try/finally 释放？
2. **资源泄漏** — 文件/连接/Client 是否在 finally 中关闭？
3. **错误处理** — 外部调用是否有 try/except？
4. **类型安全** — 函数签名是否缺少类型注解？
5. **业务逻辑** — 边界情况（None、空列表）是否处理？

## 输出格式

```
## [CRITICAL|MAJOR|MINOR] 文件名:行号 — 标题

**问题**: 描述
**建议**: 具体修复方案
```
```

根据项目语言调整审查维度。

#### 3.2 .claude/workflows/ci-monitor.md

```markdown
# ci-monitor

**Workflow**: CI 状态监控
**Trigger**: `/workflow ci-monitor`

## Steps

1. 检查 GitHub Actions 最新运行状态
2. 输出报告

GitHub repo: <user>/<repo>
```

### Step 4：创建 Memory 系统

在 `/Users/zhangyue/.claude/projects/<项目路径>/memory/` 创建：

| 文件 | 内容 |
|------|------|
| `MEMORY.md` | 索引文件 |
| `user-profile.md` | 开发者角色、技术栈、偏好 |
| `project-constraints.md` | 协作红线、架构约束 |
| `project-commands.md` | 命令速查 |
| `architecture-decisions.md` | 关键架构决策索引 |
| `known-issues.md` | 踩坑记录 |
| `recurring-tasks.md` | 例行操作 SOP |

模板直接从 ai-platform-lab 的 memory 截取，修改项目特定内容。

### Step 5：CI 基础配置

如果项目有 GitHub Actions，建议用户补充：

1. **Lint job**：跑 linter/formatter
2. **Test job**：跑测试
3. **Coverage 报告**（可选）：`--cov-report=html` + `actions/upload-artifact`
4. **Security audit**（可选）：`pip-audit` / `npm audit`

告诉用户这些文件建议手动创建，不自动生成（因为每个 CI 平台不一样）。

---

## 最佳实践清单（供参考）

初始化完成后，可以给用户一份总结：

```
✅ Harness 底座已搭建

  基础工程结构:
    CLAUDE.md        — 项目指令文件
    settings.json    — 项目级配置
    launch.json      — 一键启动
    pre-commit hook  — 提交前检查
    Justfile         — 命令别名

  Agents & Workflows:
    code-review      — 代码审查 Agent
    ci-monitor       — CI 状态监控

  Memory 持久化:
    6 个 memory 文件 — 跨会话上下文

  建议下一步:
    • 补充 .github/workflows/ci.yml 测试流程
    • 补充 docs/ 架构文档
    • 安装 just: brew install just
    • 初始化 git 仓库 & 首次 push
```

---

## 语言/框架适配

| 项目类型 | CLAUDE.md 规范 | pre-commit | Justfile 命令 |
|---------|---------------|------------|--------------|
| Python CLI | ruff, pytest | ruff-pre-commit | test/lint/type |
| FastAPI 后端 | ruff, pytest, uvicorn | ruff-pre-commit | up/test/lint |
| FastAPI + React | ruff, pytest, vite, npm | ruff + eslint | up/api/front/test |
| TypeScript lib | eslint, prettier, vitest | eslint + prettier | test/lint/build |
| Go 项目 | go fmt, golangci-lint | golangci-lint | test/lint/build |
| Rust 项目 | cargo fmt, clippy | cargo + clippy | test/lint/build |

---

## 参考

- [ai-platform-lab CLAUDE.md](https://github.com/xingyun0812/ai-platform-lab/blob/main/CLAUDE.md)
- [ai-platform-lab pre-commit config](https://github.com/xingyun0812/ai-platform-lab/blob/main/.pre-commit-config.yaml)
- [ai-platform-lab Justfile](https://github.com/xingyun0812/ai-platform-lab/blob/main/Justfile)
- [ai-platform-lab .claude/agents](https://github.com/xingyun0812/ai-platform-lab/tree/main/.claude/agents)
- [ai-platform-lab memory files](file:///Users/zhangyue/.claude/projects/-Users-zhangyue-IdeaProjects-ai-platform-lab/memory/)
