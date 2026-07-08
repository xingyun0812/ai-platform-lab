---
name: harness-project-init
description: >
  用 ai-platform-lab 的 Harness 工程最佳实践来初始化一个新项目。
  创建 CLAUDE.md、.claude/settings.json、launch.json、pre-commit hooks、
  Justfile、Agents、Workflows、Memory 系统，一条命令搭好工程底座。
  适合任何 Python/TypeScript 项目，新项目或是已有项目都可以。
---

# Harness Project Init

用 `ai-platform-lab` 沉淀的 Harness 工程最佳实践初始化一个新项目。

---

## 快速开始（推荐）

用一键安装脚本，无需 Claude：

```bash
# 在目标项目目录执行
bash <(curl -sfL https://raw.githubusercontent.com/xingyun0812/ai-platform-lab/main/scripts/harness-init.sh) <模板名>

# 示例
bash <(curl -sfL https://raw.githubusercontent.com/xingyun0812/ai-platform-lab/main/scripts/harness-init.sh) python
bash <(curl -sfL https://raw.githubusercontent.com/xingyun0812/ai-platform-lab/main/scripts/harness-init.sh) python-fastapi
bash <(curl -sfL https://raw.githubusercontent.com/xingyun0812/ai-platform-lab/main/scripts/harness-init.sh) typescript-react
bash <(curl -sfL https://raw.githubusercontent.com/xingyun0812/ai-platform-lab/main/scripts/harness-init.sh) go
bash <(curl -sfL https://raw.githubusercontent.com/xingyun0812/ai-platform-lab/main/scripts/harness-init.sh) rust
```

脚本会下载模板文件到当前目录并替换占位符。

---

## 可用模板

| 模板 | 语言 | CLAUDE.md 规范 | pre-commit | Justfile |
|------|------|---------------|------------|--------------|
| `python` | Python CLI/库 | ruff, pytest, mypy | ruff + mypy | test/lint/type |
| `python-fastapi` | FastAPI 后端 | ruff, pytest, mypy, uvicorn | ruff + mypy | up/test/lint |
| `typescript-react` | React 前端 | eslint, prettier, vitest, tsc | eslint + prettier | dev/test/lint |
| `go` | Go 服务 | gofumpt, golangci-lint | go-fmt + vet | build/test/lint |
| `rust` | Rust 项目 | cargo fmt, clippy | fmt + clippy | build/test/lint |

模板文件在 `ai-platform-lab` 仓库的 `.claude/init-templates/` 目录下，每个模板包含：

- `CLAUDE.md` — 项目指令（带 `<占位符>`）
- `.pre-commit-config.yaml` — 语言对应的 linter/formatter
- `Justfile` — 命令别名
- `scripts/setup-hooks.sh` — pre-commit 安装脚本
- `pyproject.toml.append` — Python 项目专属（需要手动追加到 pyproject.toml）

---

## 用法（Claude 交互式）

在 Claude 会话中：

```bash
# 进入目标项目目录，然后：
/skill harness-project-init
```

Claude 会引导你：

1. **选模板**：从可用列表中选择
2. **下载模板**：用 `curl` 从 ai-platform-lab 仓库下载
3. **自定义**：根据你的项目修改 CLAUDE.md 的占位符
4. **配置 Claude 专属文件**：创建 `.claude/settings.json`、`launch.json`、Agents、Workflows
5. **创建 Memory 系统**：在 `~/.claude/projects/` 下创建 memory 文件

### 指定模板参数

```bash
/skill harness-project-init python
/skill harness-project-init fastapi-react
/skill harness-project-init rust
```

---

## 安装后的建议

模板文件创建后，建议检查：

1. **CLAUDE.md** — 补充构建命令、测试命令、环境变量
2. **.pre-commit-config.yaml** — 确认 hook 版本与本地工具版本匹配
3. **Justfile** — 确认命令可执行
4. **pyproject.toml** — Python 项目手动追加 `pyproject.toml.append` 的内容
5. **Memory 文件** — Claude 自动加载，但仍需修改项目特定内容

---

## 最佳实践清单

参考 `ai-platform-lab` 的经验，一条完整的能力交付 = **代码 + gate + 文档 + demo**。

初始化后建议逐步补充：

1. `.github/workflows/ci.yml` — lint + test 流水线
2. `docs/adr/` — 非平凡架构决策记录
3. `CHANGELOG.md` — 版本历史
4. `known-issues.md` (memory) — 踩坑记录

---

## 参考链接

- [ai-platform-lab CLAUDE.md](https://github.com/xingyun0812/ai-platform-lab/blob/main/CLAUDE.md)
- [ai-platform-lab init templates](https://github.com/xingyun0812/ai-platform-lab/tree/main/.claude/init-templates)
- [ai-platform-lab scripts](https://github.com/xingyun0812/ai-platform-lab/tree/main/scripts)
