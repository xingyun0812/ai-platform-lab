# 贡献指南 — ai-platform-lab

感谢你愿意为 ai-platform-lab 贡献代码！本文档说明协作流程，让每位贡献者都能高效、规范地推进。

---

## 1. Issue 驱动开发（核心流程）

本项目采用 **Issue 驱动开发**：每个功能 / 修复都先开 Issue，再开 PR，最后打 Tag。

```
roadmap.md (规划)
    ↓
GitHub Issue (认领)
    ↓
feature branch (开发)
    ↓
Pull Request (评审)
    ↓
main + tag (合并打标)
```

### 1.1 认领 Issue

1. 在 [Issues 页面](https://github.com/xingyun0812/ai-platform-lab/issues) 选择未分配的 Issue
2. 评论 `@<维护者> 我来认领这个 Issue`，等待分配
3. 分配后 Issue 会被加上 `assigned` 标签

### 1.2 创建分支

分支命名规范：

```bash
# 功能分支
git checkout -b feat/issue-<N>-<short-name>
# 例：feat/issue-45-python-sdk

# 修复分支
git checkout -b fix/issue-<N>-<short-name>
# 例：fix/issue-42-audit-classify-bug

# 文档分支
git checkout -b docs/issue-<N>-<short-name>
```

### 1.3 开发与提交

**Commit 规范**（参考 [Conventional Commits](https://www.conventionalcommits.org/)）：

```bash
# 功能
git commit -m "feat: 实现 Python SDK 的 Agent API 封装 (closes #45)"

# 修复
git commit -m "fix: 修复 audit_actions 启发式分类误判 delete 工具"

# 文档
git commit -m "docs: 补充 Phase J SDK 使用示例"

# 测试
git commit -m "test: 为 OAuth2 callback 增加边界用例"

# 重构
git commit -m "refactor: 抽取 PII 检测的公共 pattern 加载逻辑"
```

**关键规则**：
- 一个 commit 只做一件事
- commit message 首行 ≤ 72 字符
- 关联 Issue：`(closes #N)` 或 `(refs #N)`
- `closes` 在 PR 合并后自动关闭 Issue

### 1.4 提交 PR

1. 推送到自己的 fork 或 `origin`：

   ```bash
   git push origin feat/issue-45-python-sdk
   ```

2. 在 GitHub 创建 PR，目标分支 `main`
3. PR 描述使用 [PR 模板](.github/PULL_REQUEST_TEMPLATE.md)
4. 等待 CI 通过 + 至少 1 个 Reviewer approve

### 1.5 合并打 Tag

- PR 合并到 `main` 后，Issue 自动关闭
- 一个 Phase 全部 Issue 完成后，维护者打 `phase-<letter>-<name>` 标签

---

## 2. 本地开发环境

### 2.1 依赖安装

```bash
git clone git@github.com:xingyun0812/ai-platform-lab.git
cd ai-platform-lab
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# 编辑 .env 填入真实 API key
```

### 2.2 运行测试

```bash
# 单个测试套件
python3 tests/test_orchestrator.py

# 所有测试
for t in tests/test_*.py; do python3 "$t"; done

# 启动 gateway
uvicorn apps.gateway.main:app --reload --port 8000

# 运行验收冒烟
python3 eval/acceptance_smoke.py
```

### 2.3 代码风格

- Python 3.9+ 兼容（`from __future__ import annotations` 必加）
- 格式化：`ruff format .`
- 检查：`ruff check .`
- 类型注解：所有公开函数必须有类型注解
- 测试：每个新模块至少 10 个单测

---

## 3. Issue 与 PR 模板

### 3.1 创建 Issue

在 [Issues 页面](https://github.com/xingyun0812/ai-platform-lab/issues/new/choose) 选择模板：

- **Phase Task** — Phase 规划的功能任务（如 #45-#52）
- **Feature Request** — 新功能建议
- **Bug Report** — Bug 上报

模板位于 [.github/ISSUE_TEMPLATE/](.github/ISSUE_TEMPLATE/)。

### 3.2 待创建的 Issue

[docs/issues-backlog.md](docs/issues-backlog.md) 包含 #45-#52 的完整 Issue 正文，可直接复制粘贴到 GitHub。维护者批量创建后，贡献者认领。

---

## 4. Phase 与 Issue 编号映射

| Phase | GitHub Issue 范围 | 状态 |
|-------|-------------------|------|
| A-E | 历史已合并（未开 Issue，直接 commit） | ✅ 已完成 |
| F (#29-#34) | 历史已合并 | ✅ tag `phase-f-capabilities` |
| G (#35) | 历史已合并 | ✅ tag `phase-g-embedding` |
| H (#37-#40) | 历史已合并 | ✅ tag `phase-h-agent-advanced` |
| I (#41-#44) | 历史已合并 | ✅ tag `phase-i-security` |
| **J (#45-#48)** | **待创建 Issue** | ⏳ |
| **K (#49-#52)** | **待创建 Issue** | ⏳ |

> **注意**：roadmap.md 中的 `#29`-`#52` 是规划编号。从 Phase J 开始，GitHub Issue 编号 = GitHub 实际分配的编号（不一定是 45）。

---

## 5. 协作约定

### 5.1 代码评审

- **必评**：所有 PR 至少 1 个 Reviewer
- **重点**：类型安全、错误处理、测试覆盖、文档同步
- **不合**：无测试、改 `main.py` 不加 router、破坏向后兼容

### 5.2 向后兼容

- 新功能必须 **opt-in**（默认 `false`），不影响现有行为
- 修改 `packages/agent/hitl.py` 等 shim 时，保持接口不变
- 删除公开 API 需先标记 `@deprecated` 至少 1 个 Phase

### 5.3 文档同步

每个新 Issue 必须包含：
- `docs/phase-<letter>-<area>.md` — 设计文档
- `README.md` 更新（如适用）
- `docs/roadmap.md` 标记 ✅
- `.env.example` 新增配置项

### 5.4 测试要求

- 单测文件：`tests/test_<area>.py`
- 用例数：≥ 10 个（功能复杂则 ≥ 15）
- Python 3.9 兼容（用 `importlib.util` 加载模块避免 dataclass 链问题）
- 所有测试必须能在无外部依赖（无 LLM API、无 Postgres、无 Redis）下通过

---

## 6. 紧急联系

- **维护者**：@xingyun0812
- **问题反馈**：[Issues](https://github.com/xingyun0812/ai-platform-lab/issues)
- **讨论**：[Discussions](https://github.com/xingyun0812/ai-platform-lab/discussions)

---

## 7. FAQ

**Q: 我可以不开 Issue 直接提 PR 吗？**
A: 小修复（typo、文档错字）可以。功能/修复必须先开 Issue，否则 PR 会被关闭。

**Q: 一个 Issue 多人想做怎么办？**
A: 先评论认领，维护者按时间顺序分配。重复 PR 以先分配者为准。

**Q: Issue 做了一半做不下去怎么办？**
A: 在 Issue 评论说明卡点，加 `blocked` 或 `help-wanted` 标签，维护者协助。

**Q: 我想加新功能但不在 roadmap 里？**
A: 用 Feature Request 模板开 Issue，描述场景与价值。维护者评估后纳入 roadmap 或标记 `wontfix`。

---

感谢你的贡献！每一行代码都在让这个 AI 平台变得更好。🚀
