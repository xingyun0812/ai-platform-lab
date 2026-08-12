# phase-closure

**Workflow**: Phase 收尾检查清单
**Trigger**: `/workflow phase-closure`

## Steps

1. **Verify code** — 检查 Phase 所有预期文件是否存在
2. **Run tests** — `python -m pytest tests/ -q`
3. **Run offline gates** — 执行三个离线门禁
4. **Check ADR** — 确认是否需要记录架构决策
5. **Update docs** — 更新 `docs/PROJECT_STATUS.md` 和 `CHANGELOG.md`
6. **Report** — 输出收尾检查结果

## Context

参考 `docs/closure-sop.md` 获取完整 checklist。
