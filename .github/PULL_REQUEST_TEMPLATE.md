## 变更说明

<!-- 简述这个 PR 做了什么，为什么 -->

关联 Issue: #<!-- 替换为 Issue 编号，如 45 -->
closes #<!-- 同上 -->

## 变更类型

- [ ] 新功能（feature）
- [ ] Bug 修复（fix）
- [ ] 重构（refactor，无功能变化）
- [ ] 文档（docs）
- [ ] 测试（test）
- [ ] 性能优化（perf）
- [ ] 构建/CI（build/ci）

## 验收检查

- [ ] 代码遵循 [CONTRIBUTING.md](../CONTRIBUTING.md) 规范
- [ ] 新增单测 ≥ 10 个，全部通过
- [ ] `ruff check .` 无报错
- [ ] 设计文档已更新（如适用）
- [ ] `.env.example` 已加新配置（如适用）
- [ ] `README.md` 已加章节（如适用）
- [ ] `docs/roadmap.md` 已标记 ✅（如完成 Issue）
- [ ] 向后兼容（新功能 opt-in，不破坏现有行为）

## 测试结果

<!-- 贴上测试输出，证明改动有效 -->

```bash
$ python3 tests/test_<area>.py
XX/XX passed
```

## 截图 / 演示

<!-- 如有 UI 变更或 API 响应示例，贴这里 -->

## 复核清单

- [ ] 我已自测过这些改动
- [ ] commit message 遵循 Conventional Commits
- [ ] 没有硬编码密钥 / token
- [ ] 没有提交 `.env` / `data/` / `.venv/` 等本地文件

---

**Reviewers**：@xingyun0812

**合并策略**：Squash merge（保持 main 历史整洁）
