## Problem Statement

多工具在同一 Plan/ReAct 轮次中需要被有序调度，但当前调度能力不完整，存在四类真缺口（经代码摸底 2026-08-24 确认）：

1. **互斥管控为零实现**：无工具互斥黑名单、无"互斥工具禁止同时调度"的拦截、无优先级择优。当同一轮工具调用里同时出现写同一资源（如 `create_record` + `update_record`）或冲突操作（如 `drop_table` + `get_kb_snippet`）时，系统会照常并行执行，产生资源抢占与数据竞争。
2. **默认执行轨的并行被退化成串行**：默认 backend 是 orchestrator，但 `plan_to_orchestrator_workflow` 用 `ordered_plan_steps` 把所有 step 串成一条链（start → s1 → s2 → … → end），**丢弃了 `depends_on` 的并行结构**。planner 轨已有 `plan_execution_layers`（按依赖层的 Kahn BFS 分组并行）+ `execute_plan_parallel`，但默认路径用不上。→ 无依赖、无冲突的步骤本应批量并行，实际串行执行，拖慢长任务。
3. **资源物理隔离缺失**：只有 `asyncio.Semaphore` 做逻辑并发上限，无"核心业务工具独占资源 / 普通工具共享资源"的建模。高并发下核心工具可能与普通工具互相抢占。
4. **结果聚合只 gather 不校验**：parallel 节点和 ReAct 轮次用 `asyncio.gather(return_exceptions=True)` 聚合，但**无 schema 完整性校验、无缺失补全**。某分支异常/缺失时只标记 error，不会校验结果完整性、也不会触发补全重试。

## Solution

在 orchestrator 轨上补齐四类调度能力，声明方式采用**双轨**（YAML 配置 + 工具注册元数据，YAML 优先），复用已有基础设施（state_machine 状态、idempotency 幂等、checkpoint 恢复、classifications 动作分级）：

1. **互斥管控**：声明工具互斥组 + 优先级。并行前对候选工具集做互斥检查，命中黑名单的互斥工具禁止同时调度，按优先级择优保留一个，其余推迟或串行执行。
2. **并行自动区分（默认轨恢复）**：`plan_to_orchestrator_workflow` 改为复用 `plan_execution_layers` 的依赖层分组，将无依赖、无互斥、无资源冲突的 step 归入同一 `parallel` 分支；强依赖 step 保持串行有序。让默认路径真正并行。
3. **资源隔离**：声明资源池归属（核心/共享），调度前检查资源占用，同一资源池冲突时串行或排队，避免核心工具被抢占。
4. **结果聚合校验补全**：parallel 分支/多工具完成后统一聚合，按工具声明的输出 schema 校验完整性；缺失或失败的产物触发补全逻辑（重跑该分支或调用补齐工具）。

## User Stories

1. As a 平台开发者, I want 在配置 YAML 或工具注册时声明互斥组, so that 互斥规则有唯一权威来源
2. As a 平台开发者, I want 同一轮被 LLM 同时调用的互斥工具在并行前被识别并拦截, so that 冲突操作不会被同时执行
3. As a 平台开发者, I want 命中互斥时按优先级择优保留一个工具, so that 关键路径不被低优先级工具抢占
4. As a 平台开发者, I want 被推迟的互斥工具在优先工具完成后重试, so that 任务能继续推进而非直接失败
5. As a 平台开发者, I want 互斥冲突事件被记录到 trace/audit, so that 能追溯调度冲突的决策过程
6. As a 平台开发者, I want 默认 orchestrator 执行轨对无依赖 step 自动并行, so that 长任务不用显式声明并行分支也能提速
7. As a 平台开发者, I want 强依赖 step 保持串行且前置结果校验通过后才执行后置, so that 依赖关系不被破坏
8. As a 平台开发者, I want 工具依赖仍以 `depends_on` 表达、并行分组自动推导, so that 开发者不用手写 DAG
9. As a 平台开发者, I want 声明了核心资源池的工具独占该资源, so that 高并发下核心操作不被普通工具抢占
10. As a 平台开发者, I want 普通工具在共享资源池内限流、可排队, so that 并发有界、不耗尽资源
11. As a 平台开发者, I want 并行分支执行完成后按工具输出 schema 校验完整性, so that 聚合结果是可信的而非只是拼起来
12. As a 平台开发者, I want 缺失/失败的产物自动触发补全重试, so that 一次分支异常不会静默污染最终结果
13. As a 平台管理员, I want 各调度决策（并行分组/互斥拦截/资源排队/补全）都有 trace 明细, so that 能复盘调度行为
14. As a 平台开发者, I want 互斥/资源/优先级既能用 YAML 配置也能用工具注册元数据声明（YAML 优先）, so that 满足集中治理与随工具内聚两种需求
15. As a 平台开发者, I want 所有新调度能力向后兼容, so that 未声明互斥/资源的现有工具运行行为不变
16. As a 平台开发者, I want 调度裁决逻辑是不依赖 LLM/数据库的可测试纯模块, so that 可以独立单测

## Implementation Decisions

### 执行轨归属

调度能力统一落在 **orchestrator 轨**（默认 backend）。planner 轨保持现状，作为参照/回退。并行分组算法复用 planner 轨已有的 `plan_execution_layers`，不另造轮子。

### 声明方式（双轨，YAML 优先）

新增调度元数据，两个来源，YAML 优先级高于注册元数据：

- **YAML**：新增 `config/agent_tool_scheduling.yaml`（仿 `agent_tool_routing.yaml` 结构），或在现有 `config/tool_classifications.yaml` 中扩展 `mutex_group` / `priority` / `resource_pool` / `output_schema` 字段。优先采用扩展 `tool_classifications.yaml`，因其已是"工具 → 策略元数据"的既有载体。
- **工具注册元数据**：`ToolDefinition` / 工具装饰器增加可选调度字段（`mutex_group`、`priority`、`resource_pool`、`output_schema`）。

### Modules to build / modify

**新模块：**

| 模块 | 功能 | 测试隔离性 |
|------|------|-----------|
| `packages/agent/scheduling/schedule_policy.py` | 调度策略模型：互斥组、优先级、资源池、输出 schema 定义与解析（YAML + 注册元数据合并，YAML 优先） | 纯数据 / 解析，零依赖 |
| `packages/agent/scheduling/mutex.py` | 互斥裁决：给定候选工具集，检出互斥命中、按优先级择优、产出 保留/推迟 决策 | 纯函数，零依赖 |
| `packages/agent/scheduling/resource_pool.py` | 资源池建模：核心独占/共享限流，占用-释放追踪 | 纯逻辑，可用内存实现，可测 |
| `packages/agent/scheduling/aggregator.py` | 结果聚合校验：按输出 schema 校验完整性，标记缺失/失败产物并触发补全 | 纯函数 + 可选回调，零依赖 |
| `tests/test_schedule_policy.py`, `tests/test_mutex.py`, `tests/test_resource_pool.py`, `tests/test_aggregator.py` | 四模块单测 | 零外部依赖 |

**修改模块：**

| 模块 | 改动 |
|------|------|
| `packages/agent/orchestrator/graph.py` | `parallel` 分支/`tool_call` 节点 config 支持调度元数据透传（可空，向后兼容） |
| `packages/agent/orchestrator/engine.py` | 在并行前调用 mutex 裁决、执行后调用 aggregator；资源池占用/释放钩子 |
| `packages/agent/orchestrator/nodes.py` | `_execute_tool_call` / `_execute_parallel` 接入互斥、资源、聚合钩子；`max_concurrent` 继续作为共享池限流底层 |
| `packages/agent/plan_workflow.py` | `plan_to_orchestrator_workflow` 用 `plan_execution_layers` 分组，将同层独立 step 组装成 `parallel` 分支（依赖 step 维持串行） |
| `packages/agent/tool_envelope.py` | 工具 handler 包装层透传/收集调度元数据与输出 schema |
| `packages/agent/registry.py` | `ToolDefinition` 增加可选调度字段，提供元数据读取接口 |
| `config/tool_classifications.yaml`（或新增 `agent_tool_scheduling.yaml`） | 互斥组 / 优先级 / 资源池 / 输出 schema 声明示例 |
| `packages/platform/` | 如新增 settings 项（如调度开关、默认并发下限），经 `PlatformPort` 透出，不直连 apps.gateway |

### 关键接口决策

- **互斥裁决**：输入 `候选工具名集合` → 输出 `{保留集合, 推迟列表, 冲突明细}`。无互斥声明的工具不受影响（向后兼容）。
- **优先级**：整型，越大越优先；同组冲突时保留最高优先级，其余进入推迟队列，在优先者完成后重试并入下一轮。
- **资源池**：`core`（独占，同一时刻仅一个工具持有）与 `shared`（`max_concurrent` 限流 + 排队）。未声明则归默认共享池。
- **聚合补全**：parallel 分支聚合后对每个产物用输出 schema 校验；缺失/失败者触发一次性补全重试（可配置重试次数，默认 1），仍失败则标记到 trace 并返回聚合错误，不阻塞成功分支。

### 约束（红线）

- 不改 planner 轨（保持现状）；只把其 `plan_execution_layers` 算法复用到 orchestrator。
- 不引入第三方调度库；依赖 asyncio + 现有 state_machine 状态机。
- 所有新能力默认关闭或降级为"无声明=现有行为"，保证向后兼容。

## Testing Decisions

**好测试的标准**：只测外部行为（给定候选工具集/依赖 /结果 → 得到正确裁决/分组 /校验结论），不测内部实现细节（不关心用哪种排序）。

**测试的模块**：`schedule_policy`、`mutex`、`resource_pool`、`aggregator` 四个纯函数模块 + 两个集成点（`plan_to_orchestrator_workflow` 的并行分组、orchestrator 并行执行接互斥/聚合）。

**测试方法**：

- **纯函数单测**（无 LLM/DB）：互斥裁决表驱动（同组冲突→择优、无声明→透传、多组→交叉检查）；资源池并发占用/释放/排队；聚合器 schema 缺失/失败→补全→仍失败标记。
- **orchestrator 集成**：构造带 `depends_on` 的 Plan，断言 `plan_to_orchestrator_workflow` 产出 parallel 分支而非纯串行链（对照现有 `validate_workflow`）；构造互斥工具同轮并行，断言只执行保留者。
- **回归**：现有 `validate_workflow` 对新增 parallel 分支仍通过（向后兼容不改 node_type 合法集）。

**参考先例**：`tests/test_plan_parallel.py`（DAG 依赖层分组断言）、`tests/test_orchestrator_traversal.py`（工作流遍历）、`tests/test_idempotency.py`（纯模块 + 集成接入点）、`tests/test_state_machine.py`（状态裁决表驱动）。

## Out of Scope

- 不改 planner 轨的并行/状态机/幂等实现，只复用其算法。
- 不做真实的物理进程/线程级资源隔离（如 cgroup、子进程沙箱）；本 PRD 的资源隔离指调度层的资源池建模与限流。
- 不做分布式调度器（多节点抢占、分布式锁）；单节点内 asyncio 调度。
- 不引入第三方依赖项（无 APScheduler / celery 等）。
- 不支持 DAG 层面的条件互斥表达式进阶语法（布尔/嵌套互斥组）；本期只做等平互斥组。

## Further Notes

- 依赖现状：plan 的 `depends_on` 已经建模，`plan_execution_layers`（planner 轨）已实现分层并行，幂等（S3）与状态机（S1）已接入 ReAct 循环，checkpoint（S4）与 dead-letter（S5）已就绪——本 PRD 的四块新能力全部建立在这些既有设施上。
- 优先级与互斥的组合语义需在实现阶段明确：当两个互斥工具优先级不同时，低优先级是否总是被推迟，还是仅在显式配置时才生效（默认只拦截不降级），避免静默改变开发者未声明意图。
- `plan_to_orchestrator_workflow` 改为并行分组后，需确保 `plan_step` 节点的 executor（`_execute_plan_step`）在 parallel 分支内对子上下文/输出回写与现有 `execute_subgraph` 的父上下文回写方式兼容。