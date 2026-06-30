## 变更说明

<!-- 简述这个 PR 做了什么，为什么 -->

关联 Issue: #<!-- 替换为 Issue 编号，如 156 -->
closes #<!-- 若本 PR 关闭 Issue，填同一编号；堆叠 PR 中间切片用 refs #N -->

## 变更类型

- [ ] 新功能（feature）
- [ ] Bug 修复（fix）
- [ ] 重构（refactor，无功能变化）
- [ ] 文档（docs）
- [ ] 测试（test）
- [ ] 性能优化（perf）
- [ ] 构建/CI（build/ci）

## 三线验收对齐（merge 前必填）

> **硬门禁**：`closes #N` 的 PR 在 merge 前，下面三处必须一致且可核对。  
> 禁止「代码/CI 已过、但 Issue 正文仍为 `[ ]`」就合并（参见 #156 教训）。

### 1. Issue 正文验收清单

从关联 Issue 复制每一项，在本 PR 打勾并写**一行证据**（命令 / 文件路径 / CI 链接）：

| Issue 验收项 | [ ] 已过 | 证据 |
|-------------|---------|------|
| （从 Issue 粘贴） | | |
| （从 Issue 粘贴） | | |

**merge 前动作**（关闭 Issue 的 PR 必做）：

- [ ] 已用 `gh issue edit <N> --body ...` 或等价方式，把 Issue 正文 checkbox 更新为 `[x]`
- [ ] 或在最终关闭 Issue 的 PR merge 前，确认 Issue body 与下表一致

### 2. 架构 / backlog 文档（如适用）

| 文档 | [ ] 已同步 | 位置 |
|------|-----------|------|
| `docs/architecture-deepening-todo.md` §N | | |
| `docs/issues-backlog-phase-*.md` | | |
| `docs/roadmap.md` | | |

### 3. 测试与 CI 证据

| 检查 | [ ] 已过 | 证据 |
|------|---------|------|
| 本地单测 / smoke | | 见下方「测试结果」代码块 |
| `ruff check .` | | |
| CI lint + smoke | | PR Checks 全绿链接 |
| eval gate（如触发） | | |

## 验收检查（通用）

- [ ] 代码遵循 [CONTRIBUTING.md](../CONTRIBUTING.md) 规范
- [ ] 新增/改动行为有单测或 eval gate 覆盖（架构 Issue 至少 1 条 HTTP/集成测）
- [ ] `ruff check .` 无报错
- [ ] 设计文档已更新（如适用）
- [ ] `.env.example` 已加新配置（如适用）
- [ ] `README.md` 已加章节（如适用）
- [ ] 向后兼容（新功能 opt-in，不破坏现有行为）

## 测试结果

<!-- 贴上实际运行的命令与输出 -->

```bash
$ ruff check .
# All checks passed!

$ python3 tests/test_<area>.py
# XX/XX passed
```

## 截图 / 演示

<!-- 如有 UI 变更或 API 响应示例，贴这里 -->

## 复核清单

- [ ] 我已自测过这些改动
- [ ] **三线验收表已填完**（Issue ↔ 文档 ↔ 测试/CI）
- [ ] commit message 遵循 Conventional Commits
- [ ] 没有硬编码密钥 / token
- [ ] 没有提交 `.env` / `data/` / `.venv/` 等本地文件

---

**Reviewers**：@xingyun0812

**合并策略**：Squash merge（保持 main 历史整洁）
