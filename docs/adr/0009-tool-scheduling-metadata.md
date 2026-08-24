# ADR-0009: 工具调度元数据声明与互斥优先级语义

- **Status**: accepted
- **Date**: 2026-08-24
- **Issue**: #244
- **Deciders**: xingyun0812
- **Tags**: phase-_, agent, scheduling, adr

## Context

多工具调度能力补齐（PRD #243）需要四类新元数据：互斥组、优先级、资源池归属、输出 schema。开工前有两个悬而未决的设计决策必须拍板，否则后续切片的字段命名与语义不稳定：

1. **配置载体**：这些调度元数据放哪。候选 —— (a) 扩展已有 `config/tool_classifications.yaml`（它已是"工具 → 策略元数据"载体，含 `action_level` / `requires_approval`）；(b) 新建独立 `config/agent_tool_scheduling.yaml`。
2. **互斥 + 优先级组合语义**：当两个互斥工具优先级不同时，低优先级是否"总是被推迟"，还是"默认只拦截不择优、仅显式配置 `priority` 时才择优"。风险在于避免静默改变开发者未声明意图。

## Decision

### 决策 1：扩展 `config/tool_classifications.yaml`

调度元数据声明在**现有 `config/tool_classifications.yaml`** 中扩展，不新建独立调度配置文件。新增可选字段：

```yaml
classifications:
  - tool_name: sql_query
    action_level: read_only
    requires_approval: false
    mutex_group: "db"          # 互斥组（可选）；同组工具不同时并行调度
    priority: 10               # 优先级（可选）；越大越优先，int
    resource_pool: core        # 资源池（可选）：core | shared；未声明默认 shared
    output_schema: "{rows: [array]}"   # 输出 JSON Schema 摘要（聚合校验用）
```

任一调度字段均可选；**未声明的工具在调度层视为"无约束"，行为与现状一致**（向后兼容）。双轨声明中 YAML 优先、工具注册元数据兜底。

### 决策 2：互斥冲突默认只拦截，双方均显式声明优先级才择优

互斥裁决语义：

- 同一互斥组的工具同时被选中时：
  - **双方均显式声明了 `priority`** → 保留最高优先级者，其余推迟（下一轮重试）。
  - **任一/双方未显式声明 `priority`** → **全部拦截**，推迟该轮，不自动择优。
- 理由：未声明优先级时无法判断开发者意图，自动择优会「静默选择」了某个工具，违背"避免改变未声明意图"的原则。择优只发生在开发者显式表达优先级偏好之后。

## Consequences

### Positive

- 单文件集中治理"工具 → 策略元数据"，动作分级与调度策略同源，心智统一。
- 向后兼容：调度元数据全可选，未声明工具现有行为不变。
- 择优是显式 opt-in：只有双方都声明了 `priority` 才发生，避免静默改变意图。

### Negative / trade-offs

- `tool_classifications.yaml` 随字段增多变长，需保持有序与注释。
- 双轨（YAML + 注册元数据）可能产生两处声明的同步成本，需以 YAML 为准、清晰记录优先级规则。

### Follow-up

- [ ] #245 在 `schedule_policy` 落地本 ADR 的字段命名与默认值语义
- [ ] #245 在 `config/tool_classifications.yaml` 补代表性工具（核心/普通、互斥组、带输出 schema）的声明示例
- [ ] #246 在 `mutex` 裁决实现决策 2 的「显式才择优」逻辑
- [ ] 同步 PRD #243 的 Implementation Decisions 与本文档字段语义保持一致