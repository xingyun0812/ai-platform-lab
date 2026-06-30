# 贡献指南 — ai-platform-lab

感谢你愿意为 ai-platform-lab 贡献代码！本文档说明协作流程，让每位贡献者都能高效、规范地推进。

> **硬性规定**：所有功能、修复、重构 **必须** 走 Issue → feature branch → PR → merge。**禁止** 直接向 `main` push 业务代码（维护者、贡献者、AI Agent 均适用）。Cursor 规则见 [.cursor/rules/issue-driven-workflow.mdc](.cursor/rules/issue-driven-workflow.mdc)。

---

## 1. Issue 驱动开发（核心流程 — 强制执行）

本项目采用 **Issue 驱动开发**：每个功能 / 修复都先开 Issue，再开 PR，最后打 Tag。

```
docs/roadmap.md / issues-backlog-*.md（规划）
    ↓
GitHub Issue（创建 + 认领，含验收标准）
    ↓
feature branch（从最新 main 拉出）
    ↓
Pull Request（CI 绿 + Review）
    ↓
merge main（禁止直推 main）
    ↓
phase-<name> tag（Phase 完成时）
```

### 1.0 红线（违反则 PR 关闭 / revert）

| 行为 | 是否允许 |
|------|----------|
| 无 Issue 开功能 PR | ❌ |
| `git push origin main` 提交功能/修复 | ❌ |
| 一个 PR 关闭多个无关 Issue（无依赖关系） | ❌ |
| 先合 main 再补 Issue「记账」 | ❌（历史补录见 §1.6） |
| 文档 typo、纯标点 | ✅ 可直 PR，仍建议关联 Issue |

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
3. PR 描述使用 [PR 模板](.github/PULL_REQUEST_TEMPLATE.md)，**必填「三线验收对齐」**（Issue ↔ 文档 ↔ 测试/CI）
4. 等待 CI 通过 + 至少 1 个 Reviewer approve

#### 1.4.1 merge 前三线验收对齐（`closes #N` 强制）

| 线 | 要求 |
|----|------|
| **Issue 正文** | 验收 checkbox 与 PR 表一致；关闭 Issue 前须为 `[x]` |
| **文档** | `architecture-deepening-todo.md` / backlog / `roadmap.md`（如适用）已同步 |
| **证据** | PR 贴测试命令输出；CI Checks 全绿 |

禁止：仅改代码与 `architecture-deepening-todo.md`，却 leave Issue body 全 `[ ]` 就 merge（#156 教训）。

### 1.5 合并打 Tag

- PR 合并到 `main` 后，Issue 由 `closes #N` 自动关闭
- 合并后维护者（或 Agent）**同步** `docs/issues-backlog-phase-*.md`：该 Issue 行标记 `✅ #<PR>`
- 一个 Phase **全部** Issue 完成后，维护者在 **`main` 最新 HEAD** 打 `phase-<letter>-<name>` 标签并 push
- **Tag 打在 merge 后的 `main` 上**，不打在 feature branch；Phase 未完成时不打 Phase tag

#### 1.5.1 Issue / Milestone / Label 清单（Phase 交付）

| 步骤 | 要求 |
|------|------|
| 开 Issue | 正文来自 backlog；挂 **Milestone**；打 `phase-task` + `phase-<letter>` |
| 开分支 | `feat/issue-<N>-<short-name>`，从最新 `main` 拉出 |
| 开 PR | 标题含 Issue 主题；body 写 `closes #N`；填三线验收对齐表 |
| 合并前 | Issue 正文 `[x]` + 文档同步 + CI/测试证据（§1.4.1） |
| 合并后 | Issue 关闭；backlog 更新 PR 号；必要时更新 `roadmap.md` / Phase 规划 doc |
| Phase 收尾 | 全部 Issue ✅ 后：`git tag -a phase-x-name -m "..." && git push origin phase-x-name` |

### 1.6 历史补录（仅维护者一次性操作）

若因失误已直推 `main`，**不得** 仅补 Issue 了事。维护者应：

1. 在 GitHub 补开对应 Issue（正文来自 `docs/issues-backlog-*.md`）
2. 从直推前 commit 建 `backup/*` 分支保留现场
3. `main` 回退到直推前，按 Issue **拆成堆叠 PR** 重新合并
4. 在 Issue 评论中附上备份分支与最终 PR 链接

Phase M（增量索引）按此流程补录：`backup/phase-m-pre-split`，Issue #63–#66。

---

## 2. 本地开发环境

### 2.1 依赖安装

项目通过 `.python-version` 锁定 Python 3.11，CI 也用 3.11。推荐用 **uv**（比 pip 快 10-100x）：

```bash
git clone git@github.com:xingyun0812/ai-platform-lab.git
cd ai-platform-lab

# 方式一：uv（推荐）
curl -LsSf https://astral.sh/uv/install.sh | sh   # 首次安装 uv
uv venv && uv pip install -e ".[dev]"

# 方式二：venv + pip（需本机已有 python3.11）
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

source .venv/bin/activate   # 激活虚拟环境
cp .env.example .env
# 编辑 .env 填入真实 API key
```

> **注意**：代码使用 `datetime.UTC` 等 3.11+ 语法，**不支持 Python 3.9/3.10**。如本机只有 3.9，`uv venv` 会自动下载 3.11。

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
| **L (#53-#63)** | #37-#47 等 | ✅ tag `phase-l-engineering-depth` |
| **M (M1-M4)** | [#63](https://github.com/xingyun0812/ai-platform-lab/issues/63)–[#66](https://github.com/xingyun0812/ai-platform-lab/issues/66) | ✅ PR [#68](https://github.com/xingyun0812/ai-platform-lab/pull/68)–[#71](https://github.com/xingyun0812/ai-platform-lab/pull/71) 堆叠补录 |
| **N (N1-N4)** | 见 [issues-backlog-phase-n.md](docs/issues-backlog-phase-n.md) | ⏳ 规划中 |

> **注意**：roadmap 中的 `#NN` 为规划编号；GitHub Issue 以实际分配为准。从 Phase J 起，**每个 Issue 必须对应一个 PR**。

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
A: **不可以**（功能/修复/重构）。仅文档错别字等极小改动可例外，仍建议开 Issue 或 Discussion。AI Agent 同样必须遵守。

**Q: 我可以直接 push 到 main 吗？**
A: **不可以**。必须 feature branch → PR → merge。维护者也不例外。

**Q: 已经误推到 main 了怎么办？**
A: 见 §1.6 历史补录：backup 分支 + 回退 main + 堆叠 PR 重放。

**Q: 一个 Issue 多人想做怎么办？**
A: 先评论认领，维护者按时间顺序分配。重复 PR 以先分配者为准。

**Q: Issue 做了一半做不下去怎么办？**
A: 在 Issue 评论说明卡点，加 `blocked` 或 `help-wanted` 标签，维护者协助。

**Q: 我想加新功能但不在 roadmap 里？**
A: 用 Feature Request 模板开 Issue，描述场景与价值。维护者评估后纳入 roadmap 或标记 `wontfix`。

---

感谢你的贡献！每一行代码都在让这个 AI 平台变得更好。🚀
