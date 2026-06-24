# Phase Q · Q3 — Replan on Failure via Critic LLM

> **Issue**: #118 · **Branch**: `feat/issue-118-replan-critic`
> **Status**: Implemented

---

## 1. 设计要点

当 `execute_plan_with_agent` 或 `execute_plan_parallel` 执行某 step 时失败（status=`failed` 或抛出异常），系统会调用 **Critic LLM** 对 Plan 进行局部修订，而不是直接放弃整个 Plan。

核心流程：

```
step 失败
  └─ attempt < max_replan_attempts ?
       ├─ Yes: 调用 replan_after_failure()
       │         ├─ critic LLM 返回修订后的完整 Plan
       │         │     └─ 记录 plan_revisions 条目
       │         │     └─ 递归调用 execute_plan_with_agent(attempt+1)
       │         └─ critic 返回 None（超限/解析失败/upstream 错误）
       │               └─ 终止 plan，status=failed
       └─ No: 终止 plan，status=failed（plan_revisions=[]）
```

### 触发条件
- step 运行结果 `status == "failed"`
- step 运行抛出未预期 Exception（catch → 包装为 failed 结果）

### 停止条件
1. `attempt >= max_replan_attempts`（默认 2）
2. critic LLM 调用失败 / upstream 返回非 200
3. critic 输出无法解析为合法 JSON 或合法 AgentPlan
4. model 不在 allowed_models 白名单

### plan_revisions 结构

每次成功触发重规划（critic 返回非 None）时，在返回 dict 的 `plan_revisions` 列表中追加一条：

```python
{
    "attempt": 1,            # 第几次重规划（从 1 起）
    "failed_step_id": "s2",  # 触发重规划的 step id
    "new_plan_steps_count": 3,  # 修订后 Plan 的 step 总数
}
```

最终所有执行结果（`execute_plan_with_agent` / `execute_plan_parallel`）都会携带 `plan_revisions: list[dict]`，成功无重规划时为 `[]`。

---

## 2. 数据模型

### AgentPlan（来自 packages/contracts/agent_schemas.py，无改动）

```python
class AgentPlan(BaseModel):
    goal: str
    steps: list[PlanStep]

class PlanStep(BaseModel):
    id: str
    description: str
    tool_hint: str | None = None
    agent_hint: str | None = None
    depends_on: list[str]
```

### PlanRevision（inline dict，记录在 trace 中）

| 字段 | 类型 | 说明 |
|---|---|---|
| `attempt` | `int` | 第几次重规划，从 1 起 |
| `failed_step_id` | `str` | 触发重规划的 step id |
| `new_plan_steps_count` | `int` | critic 修订后 Plan 的 step 数量 |

---

## 3. REST API

本 Issue 无新增 API endpoint。修改体现在现有 `execute_plan_with_agent` 和 `execute_plan_parallel` 的返回结构中：

| 字段 | 类型 | 说明 |
|---|---|---|
| `plan_revisions` | `list[dict]` | 每次 critic replan 的记录（新增字段） |

现有 `AgentRunResponse` schema 中如需暴露此字段，父 Agent 可在 `packages/contracts/agent_schemas.py` 的 `AgentRunResponse` 中添加：

```python
plan_revisions: list[dict[str, Any]] | None = None
```

---

## 4. 配置表

### 函数参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `max_replan_attempts` | `2` | 最大重规划次数；0 表示禁用 critic |
| `_replan_attempt` | `0` | 内部递归计数（请勿外部传入） |
| `_plan_revisions` | `None` | 内部传递列表（请勿外部传入） |

### config/prompts.yaml 新增条目

```yaml
agent_plan_critic:
  version: 1
  template: |
    原始 Plan（JSON）：
    {plan_json}
    失败的 step（id={failed_step_id}）：{failed_step_desc}
    失败原因：{failure_reason}
    {context_block}
    请输出修订后的完整 Plan（JSON），修复失败 step 或替换为可执行的步骤。
    只输出合法 JSON，不要其他文字。
```

（实际已写入 `config/prompts.yaml`，prompt_id 为 `agent_plan_critic`，可通过 Prompt Registry API 覆盖）

---

## 5. 测试

文件：`tests/test_plan_critic.py`（21 个测试用例）

| 测试用例 | 类别 |
|---|---|
| `test_build_critic_prompt_contains_plan` | build_critic_prompt |
| `test_build_critic_prompt_contains_failure_reason` | build_critic_prompt |
| `test_build_critic_prompt_with_context` | build_critic_prompt |
| `test_build_critic_prompt_without_context` | build_critic_prompt |
| `test_build_critic_prompt_contains_step_description` | build_critic_prompt |
| `test_build_critic_prompt_plan_json_in_output` | build_critic_prompt |
| `test_plain_json` | _extract_json_from_text |
| `test_fenced_json` | _extract_json_from_text |
| `test_invalid_json_returns_none` | _extract_json_from_text |
| `test_empty_returns_none` | _extract_json_from_text |
| `test_replan_returns_none_when_max_attempts_reached` | replan_after_failure |
| `test_replan_returns_none_when_attempt_exceeds_max` | replan_after_failure |
| `test_replan_success_returns_agent_plan` | replan_after_failure |
| `test_replan_critic_parse_failure_returns_none` | replan_after_failure |
| `test_replan_critic_upstream_error_returns_none` | replan_after_failure |
| `test_replan_model_not_allowed_returns_none` | replan_after_failure |
| `test_execute_plan_with_agent_triggers_replan_on_failure` | integration |
| `test_execute_plan_with_agent_no_replan_on_max_attempts` | integration |
| `test_plan_revisions_format` | integration |
| `test_execute_plan_completed_has_empty_plan_revisions` | integration |
| `test_execute_plan_critic_returns_none_terminates_failed` | integration |

运行：

```bash
.venv/bin/python tests/test_plan_critic.py -v
# Ran 21 tests in 0.038s — OK
```

---

## 6. 代码导航

| 文件 | 说明 |
|---|---|
| `packages/agent/plan_critic.py` | 新增：Critic 核心逻辑（`build_critic_prompt`, `replan_after_failure`, `_call_upstream`, `_check_model_allowed`, `_extract_json_from_text`） |
| `packages/agent/planner.py` | 修改：`execute_plan_with_agent`（新增 `max_replan_attempts`, `_replan_attempt`, `_plan_revisions` 参数；集成 replan 逻辑；返回 `plan_revisions`）；`execute_plan_parallel`（同上） |
| `config/prompts.yaml` | 修改：新增 `agent_plan_critic` prompt 条目 |
| `tests/test_plan_critic.py` | 新增：21 个测试用例 |

---

## 7. 父 Agent 集成说明（shared files）

### `packages/contracts/agent_schemas.py`（可选扩展）

在 `AgentRunResponse` 中添加字段：

```python
plan_revisions: list[dict[str, Any]] | None = None
```

### `apps/gateway/settings.py`（可选新增）

```python
# Replan Critic 相关配置（可选）
replan_max_attempts: int = Field(
    default=2,
    validation_alias="REPLAN_MAX_ATTEMPTS",
    description="execute_plan 失败时最大重规划次数；0=禁用 critic",
)
```

### `.env.example`（可选新增）

```bash
# Phase Q Q3 — Replan Critic
REPLAN_MAX_ATTEMPTS=2
```

### `README.md` 补充段落

```markdown
### Q3 · Replan on Failure (Issue #118)

步骤失败时 Critic LLM 自动修订 Plan，最多重规划 `REPLAN_MAX_ATTEMPTS`（默认 2）次。
每次重规划记录在返回结果的 `plan_revisions` 字段中（attempt / failed_step_id / new_plan_steps_count）。
```

### `docs/roadmap.md` 追加

```markdown
- [x] Q3 Replan on failure via Critic LLM — `packages/agent/plan_critic.py` (#118)
```

---

## 8. 已知限制

1. **局部 patch vs 全量重规划**：当前 critic 被要求输出完整修订 Plan，而非仅替换失败 step。更精细的"只修改一个 step"可在后续迭代优化。
2. **递归深度**：`execute_plan_with_agent` 使用真正的 Python 递归调用。在 `max_replan_attempts` 较大（>5）时，可改为迭代实现以避免栈溢出。
3. **并行执行 layer 选代表 step**：`execute_plan_parallel` 目前用 `layer[0]` 作为"失败代表 step"传入 critic，若层内多个 step 失败，critic 只收到第一个失败的 step 信息。后续可将所有失败 step 汇总传入。
4. **plan_revisions 未写入 AgentRunResponse schema**：当前作为 raw dict 字段返回，父 Agent 集成时可选择性地添加到 schema。

---

## 9. 面试谈话要点

1. **为什么是 Critic 而不是重试同一 step？**  
   直接重试假设是瞬时错误（如网络超时），而 Critic LLM 可以理解失败原因并生成结构不同的替代步骤（例如将"直接调用不可用 API"改为"通过缓存获取数据"），解决的是计划本身的问题。

2. **如何防止无限重规划循环？**  
   `max_replan_attempts`（默认 2）硬限制重规划次数，通过 `attempt` 计数在每次递归调用时传入，超限后直接返回 `None` 降级为 failed。

3. **降级策略**  
   `replan_after_failure` 在以下情况均返回 `None`：LLM 调用失败、输出非法 JSON、Plan 校验失败、模型不在白名单。调用方（planner）收到 None 时直接终止 Plan 为 failed，不 crash。

4. **plan_revisions 的可观察性价值**  
   每次重规划的信息（attempt、failed_step_id、new_plan_steps_count）被追加到返回 dict，可用于后续 trace 分析、成本核算（critic 调用次数）和调试。

5. **为什么 `_call_upstream` 和 `_check_model_allowed` 独立为函数？**  
   封装延迟导入（避免模块顶层 import 触发完整 app 链），同时提供独立的 mock 目标，让单测不依赖真实 LLM/model_router。
