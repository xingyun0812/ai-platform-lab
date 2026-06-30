# ADR-0003: AppContext 统一 Gateway 单例装配

- **Status**: accepted
- **Date**: 2026-06-11
- **Issue**: [#178](https://github.com/xingyun0812/ai-platform-lab/issues/178)
- **Tags**: phase-r, gateway, architecture-deepening, singleton
- **Supersedes**: 无（补充 [ADR-0001](./0001-three-id-boundaries.md) 的 PlatformPort 模式）

## Context

Gateway 启动时通过 `wire_gateway_dependencies(settings)` 按 feature flag 调用 15+ 个 `init_*`，各包内再暴露 `get_*` 全局单例。问题：

1. **命名冲突**：`packages.prompt.get_registry()` 与 `packages.embedding.get_registry()` 语义不同，import 时易混淆。
2. **静默降级**：`packages/feedback/api.py` 在 store 未 init 时自动 `InMemoryFeedbackStore()`，与 gateway `feedback_enabled` 门控不一致。
3. **测试 teardown 分散**：各包 `reset_*_for_tests()` 需逐模块调用。

`packages.platform.configure()` 已为 PlatformPort 提供注入模式；Gateway 侧 Phase store 仍缺统一容器。

## Decision

### AppContext 作为 Gateway 装配入口

```python
# apps/gateway/lifespan.py
settings = get_settings()
wire_platform()
ctx = build_app_context(settings)
ctx.wire()
```

- `AppContext` 持有 `settings: Settings` 与 `wired: bool`。
- `wire()` 委托现有 `wire_gateway_dependencies(settings)`（PR-8a 不搬迁全部 init 逻辑，避免大爆炸）。
- `AppContext.test(**overrides)`：测试前 `reset_all_for_tests()` + Settings override。

### 命名消歧

| 包 | 首选 API | 兼容 alias |
|----|----------|------------|
| prompt | `get_prompt_registry()` | `get_registry()` |
| embedding | `get_embedding_registry()` | `get_registry()` |

新代码必须使用带前缀名称；旧名保留 alias，不破坏现有调用。

### Feedback 显式失败

`record_feedback` / `get_feedback` / `list_feedback` 在 store 未 init 时 `raise RuntimeError`，不再静默 InMemory。生产路径：`feedback_enabled=True` → lifespan `init_feedback_store()`。

### reset_all_for_tests 骨架

`apps/gateway/app_context.reset_all_for_tests()` 按 `wire()` 触达的模块顺序调用各包 reset；后续 PR 可扩展为 AppContext 字段持有 store 引用（真正构造期注入）。

## Consequences

- **Positive**：lifespan 语义清晰；测试一条 `reset_all_for_tests()`；registry 命名可区分。
- **Negative**：各包仍保留模块级 `_store`（PR-8a 未消除单例，仅统一装配与测试入口）；完全 DI 需后续 PR-8b+。
- **Follow-up**：#9 gateway 双轨统一可依赖本 ADR；可选 PR-8b 将 hot path store 迁入 AppContext 字段。

## References

- `docs/architecture-deepening-todo.md` §8
- `apps/gateway/app_context.py`
- `apps/gateway/composition.py`
