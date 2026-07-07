# ci-monitor

**Workflow**: CI 状态监控
**Trigger**: `/workflow ci-monitor`

## Steps

1. **Check CI Status** — 调用 GitHub Actions API 检查 `ci.yml` 和 `eval.yml` 最新运行状态
2. **Report** — 输出检查结果到终端

## Output

```
CI workflow ci.yml: ✅ success (run #xxx, branch main, 3m42s)  
Eval workflow eval.yml: 🟡 in_progress...  
Live gate: — (workflow_dispatch only, no recent trigger)
Publish SDK: — (no recent tag push)
```

## Context

GitHub repo: xingyun0812/ai-platform-lab

Workflow files:
- `.github/workflows/ci.yml` — Full CI (lint + smoke)
- `.github/workflows/eval.yml` — Eval gate suite
- `.github/workflows/live-gate.yml` — Manual E2E live gate
- `.github/workflows/publish-sdk.yml` — PyPI publish
