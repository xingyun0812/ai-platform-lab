# Architecture Decision Records (ADR)

轻量架构决策记录。重大边界、不可合并的 ID、安全策略等写一页，避免只在聊天里说过就忘。

| ADR | 标题 | 状态 |
|-----|------|------|
| [0001](0001-three-id-boundaries.md) | Phase Q7 / R2 三张 ID 边界 | accepted |
| [0002](0002-checkpoint-resume-layers.md) | 三套 Checkpoint/Resume 层级 | accepted |
| [0003](0003-app-context-singletons.md) | AppContext 统一 Gateway 单例装配 | accepted |
| — | [TEMPLATE.md](TEMPLATE.md) | 新 ADR 从此复制 |

**何时写 ADR**

- 两个模块职责重叠、必须划边界（如 `plan_approval_id` vs `task_id`）
- 不可逆或难改的数据模型 / API 字段名
- 安全或 HITL 策略（什么不能自动改）

**流程**：复制 `TEMPLATE.md` → `docs/adr/NNNN-short-title.md` → PR 关联 Issue → 在相关 `phase-*.md` 里链接。
