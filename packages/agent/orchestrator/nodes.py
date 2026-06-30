"""节点执行器 — 按 node_type 分派执行逻辑。"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("ai_platform.orchestrator.nodes")


NodeExecutor = Callable[[dict[str, Any], Any], Awaitable[Any]]
"""节点执行器签名：async def(ctx_config, execution_context) -> output"""


class NodeExecutorError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class ConditionBranch:
    """条件分支。"""
    condition: str
    target: str


@dataclass
class ParallelBranch:
    """并行分支。"""
    id: str
    subgraph: Any  # Workflow


@dataclass
class LoopBody:
    """循环体。"""
    subgraph: Any  # Workflow
    max_iterations: int
    break_condition: str | None


# --------------------------------------------------------------------- #
# 节点执行器注册表
# --------------------------------------------------------------------- #

_EXECUTORS: dict[str, NodeExecutor] = {}


def register_node_executor(node_type: str, executor: NodeExecutor) -> None:
    _EXECUTORS[node_type] = executor


def get_executor(node_type: str) -> NodeExecutor | None:
    return _EXECUTORS.get(node_type)


# --------------------------------------------------------------------- #
# 模板渲染：${node_id.field} 或 ${variable}
# --------------------------------------------------------------------- #

_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def render_template(template: str, context: Any) -> str:
    """渲染 ${node_id.field} 或 ${variable} 模板。

    context 需提供 .outputs: dict[str, Any] 和 .variables: dict[str, Any]
    """
    def _replace(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        value = resolve_reference(expr, context)
        return str(value) if value is not None else ""

    return _VAR_PATTERN.sub(_replace, template)


def resolve_reference(expr: str, context: Any) -> Any:
    """解析引用表达式：node_id.field.subfield 或 variable_name"""
    parts = expr.split(".")
    root = parts[0]
    # 先查 outputs，再查 variables，再查 inputs
    value: Any = None
    if hasattr(context, "outputs") and root in context.outputs:
        value = context.outputs[root]
    elif hasattr(context, "variables") and root in context.variables:
        value = context.variables[root]
    elif hasattr(context, "inputs") and root in context.inputs:
        value = context.inputs[root]
    # 逐层下钻
    for part in parts[1:]:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list):
            try:
                idx = int(part)
                value = value[idx] if 0 <= idx < len(value) else None
            except (ValueError, IndexError):
                value = None
        else:
            value = None
        if value is None:
            break
    return value


# --------------------------------------------------------------------- #
# 条件表达式求值（沙箱）
# --------------------------------------------------------------------- #

# 允许的运算符
_ALLOWED_OPS = {
    "==", "!=", ">=", "<=", ">", "<",
    "and", "or", "not", "True", "False", "None",
    "in", "is",
}


def evaluate_condition(expr: str, context: Any) -> bool:
    """安全求值条件表达式。

    支持：
        - 比较：==, !=, >, <, >=, <=
        - 布尔：and, or, not
        - 引用：${node_id.field}（先渲染为值）
        - 字面量：数字、字符串、True/False/None

    禁止：import、exec、eval 嵌套、任意函数调用
    """
    # 渲染 ${...} 引用为字面量
    def _replace_with_literal(match: re.Match[str]) -> str:
        ref_expr = match.group(1).strip()
        value = resolve_reference(ref_expr, context)
        return _to_python_literal(value)

    rendered = _VAR_PATTERN.sub(_replace_with_literal, expr)
    # 简单词法检查：禁止危险关键字
    forbidden = ["import", "exec", "eval", "open", "__", "lambda", "globals", "locals"]
    lowered = rendered.lower()
    for kw in forbidden:
        if kw in lowered:
            raise NodeExecutorError(
                "FORBIDDEN_KEYWORD", f"条件表达式包含禁止关键字: {kw}"
            )
    try:
        # 使用 eval 但限制全局/局部命名空间
        result = eval(  # noqa: S307 — 已做词法过滤
            rendered,
            {"__builtins__": {}},
            {"True": True, "False": False, "None": None},
        )
        return bool(result)
    except Exception as e:
        logger.warning("condition eval failed expr=%r err=%s", expr, e)
        return False


def _to_python_literal(value: Any) -> str:
    """将值转为 Python 字面量字符串。"""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # 转义引号
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (list, dict)):
        import json
        return json.dumps(value, ensure_ascii=False)
    return f'"{str(value)}"'


# --------------------------------------------------------------------- #
# 内置节点执行器
# --------------------------------------------------------------------- #

async def _execute_start(config: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return {"status": "started"}


async def _execute_end(config: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return {"status": "completed"}


async def _execute_llm_call(config: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """调用 LLM。

    config:
        prompt: str           # 支持 ${var} 模板
        model: str | None
        variables: dict       # 额外变量
    """
    from packages.platform import forward_with_model_router, get_settings

    settings = get_settings()
    prompt_template = str(config.get("prompt", ""))
    rendered_prompt = render_template(prompt_template, ctx)
    model = config.get("model") or settings.default_model
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": rendered_prompt},
        ],
        "temperature": 0.2,
    }
    routed = await forward_with_model_router(payload, requested_model=model)
    if routed.body is None or not (200 <= routed.status < 300):
        raise NodeExecutorError(
            "LLM_CALL_FAILED",
            f"LLM 调用失败 status={routed.status} error={routed.error}",
        )
    choices = routed.body.get("choices") or []
    if not choices:
        return {"content": "", "usage": routed.body.get("usage", {})}
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = msg.get("content", "") if isinstance(msg, dict) else ""
    return {
        "content": content,
        "model": routed.model_used or model,
        "usage": routed.body.get("usage", {}),
    }


async def _execute_tool_call(config: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """调用 Agent 工具。"""
    from packages.agent.registry import ToolRegistry

    tool_name = str(config.get("tool_name", ""))
    arguments = config.get("arguments", {})
    if isinstance(arguments, dict):
        # 渲染模板
        rendered_args: dict[str, Any] = {}
        for k, v in arguments.items():
            if isinstance(v, str):
                rendered_args[k] = render_template(v, ctx)
            else:
                rendered_args[k] = v
    else:
        rendered_args = {}
    registry = ToolRegistry()
    tool = registry.get(tool_name)
    if tool is None:
        raise NodeExecutorError(
            "TOOL_NOT_FOUND", f"工具 {tool_name} 不存在"
        )
    try:
        result = await tool.handler(rendered_args)
        return {"result": result, "tool": tool_name}
    except Exception as e:
        raise NodeExecutorError(
            "TOOL_CALL_FAILED", f"工具 {tool_name} 调用失败: {e}"
        ) from e


async def _execute_condition(config: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """条件节点：评估各分支，返回选中的 target。

    注意：实际跳转由 engine 处理（engine 读取 condition 节点的 output.branch）
    """
    branches = config.get("branches", [])
    default = config.get("default")
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        cond = str(branch.get("condition", ""))
        target = str(branch.get("target", ""))
        if not cond or not target:
            continue
        if evaluate_condition(cond, ctx):
            return {"branch": target, "matched": cond}
    return {"branch": default, "matched": "default"}


async def _execute_parallel(config: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """并行执行多个子图分支。"""
    from packages.agent.orchestrator.engine import execute_subgraph

    branches = config.get("branches", [])
    gather_mode = str(config.get("gather", "all"))  # all | first
    max_concurrent = int(config.get("max_concurrent", 5))

    tasks = []
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        subgraph_data = branch.get("subgraph")
        if not isinstance(subgraph_data, dict):
            continue
        tasks.append(
            execute_subgraph(subgraph_data, ctx, branch.get("id", "branch"))
        )
    # 限制并发
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _run_with_limit(coro):
        async with semaphore:
            return await coro

    limited_tasks = [_run_with_limit(t) for t in tasks]
    if gather_mode == "first":
        # 第一个完成即返回
        done, pending = await asyncio.wait(
            limited_tasks, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        result = done.pop().result()
        return {"result": result, "gather": "first"}
    # all：等待全部
    results = await asyncio.gather(*limited_tasks, return_exceptions=True)
    outputs = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            outputs.append({"error": str(r), "branch_id": branches[i].get("id")})
        else:
            outputs.append(r)
    return {"results": outputs, "gather": "all"}


async def _execute_loop(config: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """循环节点：重复执行 body 直到 break_condition 或 max_iterations。"""
    from packages.agent.orchestrator.engine import execute_subgraph

    body_data = config.get("body")
    max_iter = int(config.get("max_iterations", 10))
    break_cond = config.get("break_condition")

    iterations = 0
    last_output: Any = None
    for i in range(max_iter):
        ctx.variables["loop_iteration"] = i
        try:
            last_output = await execute_subgraph(body_data, ctx, f"loop_{i}")
        except Exception as e:
            return {"iterations": i, "error": str(e)}
        iterations = i + 1
        if break_cond and evaluate_condition(str(break_cond), ctx):
            break
    return {"iterations": iterations, "last_output": last_output}


async def _execute_output(config: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """输出节点：渲染模板并返回。"""
    value_template = str(config.get("value", ""))
    rendered = render_template(value_template, ctx)
    return {"value": rendered}


async def _execute_plan_step(config: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """Plan step 节点：执行 AgentPlan 中的单步（调用 run_agent）。"""
    from packages.agent.planner import format_step_user_message
    from packages.agent.runner import run_agent
    from packages.contracts.agent_schemas import PlanStep

    step_id = str(config.get("step_id") or "")
    description = str(config.get("description") or "").strip()
    if not description:
        raise NodeExecutorError("INVALID_CONFIG", "plan_step 需要 description")

    step = PlanStep(
        id=step_id or "step",
        description=description,
        tool_hint=config.get("tool_hint"),
        agent_hint=config.get("agent_hint"),
        depends_on=[],
    )
    step_index = int(config.get("step_index") or 1)
    step_total = int(config.get("step_total") or 1)
    step_msg = format_step_user_message(step, index=step_index, total=step_total)

    inputs = getattr(ctx, "inputs", {}) or {}
    tenant_id = str(inputs.get("tenant_id") or "admin")
    session_id = str(inputs.get("session_id") or "plan-session")
    sub_session = f"{session_id}__step_{step_id or step_index}"
    allowed_tools_raw = inputs.get("allowed_tools")
    allowed_tools = (
        tuple(allowed_tools_raw)
        if isinstance(allowed_tools_raw, (list, tuple))
        else tuple()
    )
    allowed_models_raw = inputs.get("allowed_models")
    allowed_models = (
        tuple(allowed_models_raw)
        if isinstance(allowed_models_raw, (list, tuple))
        else tuple()
    )
    model = inputs.get("model")
    session_store = inputs.get("session_store")
    step_system_messages = inputs.get("step_system_messages")
    new_messages: list[dict[str, Any]] = [{"role": "user", "content": step_msg}]
    if step_system_messages and step_index == 1:
        new_messages = [*step_system_messages, *new_messages]

    pinned = (step.tool_hint,) if step.tool_hint else None
    try:
        result = await run_agent(
            tenant_id=tenant_id,
            session_id=sub_session,
            new_messages=new_messages,
            allowed_tools=allowed_tools,
            allowed_models=allowed_models,
            model=model,
            session_store=session_store,
            pinned_tools=pinned,
        )
    except Exception as exc:
        raise NodeExecutorError(
            "PLAN_STEP_FAILED",
            f"plan_step {step_id} 执行失败: {exc}",
        ) from exc

    status = str(result.get("status") or "completed")
    if status == "failed":
        raise NodeExecutorError(
            "PLAN_STEP_FAILED",
            str(result.get("final_message") or f"plan_step {step_id} failed"),
        )

    return {
        "step_id": step_id,
        "status": status,
        "final_message": result.get("final_message"),
        "tool_calls": result.get("tool_calls") or [],
        "model": result.get("model"),
        "approval_id": result.get("approval_id"),
    }


async def _execute_agent_call(config: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """Agent 委托节点：调用子 Agent 执行任务。

    config:
        agent_id: str           # 子 Agent ID
        task: str                # 任务描述（支持 ${var} 模板）
        inputs: dict             # 额外输入（支持模板）
        timeout: float           # 超时（默认 60s）
    """
    from packages.agent.multi_agent import delegate_to_agent

    agent_id = str(config.get("agent_id", ""))
    if not agent_id:
        raise NodeExecutorError("INVALID_CONFIG", "agent_call 需要 agent_id")
    task_template = str(config.get("task", ""))
    task = render_template(task_template, ctx)
    inputs_cfg = config.get("inputs", {})
    if isinstance(inputs_cfg, dict):
        rendered_inputs: dict[str, Any] = {}
        for k, v in inputs_cfg.items():
            if isinstance(v, str):
                rendered_inputs[k] = render_template(v, ctx)
            else:
                rendered_inputs[k] = v
    else:
        rendered_inputs = {}
    timeout = float(config.get("timeout", 60.0))
    use_blackboard = config.get("use_blackboard", True)
    # 委托栈：从 ctx 继承（如果存在）
    delegation_stack = getattr(ctx, "variables", {}).get("_delegation_stack", [])
    tenant_id = str(getattr(ctx, "inputs", {}).get("tenant_id") or "admin")
    session_id = getattr(ctx, "inputs", {}).get("session_id")
    if session_id is not None:
        session_id = str(session_id)
    allowed_tools_raw = getattr(ctx, "inputs", {}).get("allowed_tools")
    allowed_tools = tuple(allowed_tools_raw) if isinstance(allowed_tools_raw, (list, tuple)) else None
    allowed_models_raw = getattr(ctx, "inputs", {}).get("allowed_models")
    allowed_models = (
        tuple(allowed_models_raw) if isinstance(allowed_models_raw, (list, tuple)) else None
    )
    result = await delegate_to_agent(
        agent_id=agent_id,
        task=task,
        tenant_id=tenant_id,
        session_id=session_id,
        inputs=rendered_inputs,
        delegation_stack=list(delegation_stack),
        timeout_seconds=timeout,
        allowed_tools=allowed_tools,
        allowed_models=allowed_models,
        use_blackboard=bool(use_blackboard),
    )
    return {
        "agent_id": agent_id,
        "task": task,
        "status": result.status,
        "output": result.output,
        "error": result.error,
        "usage": result.usage,
        "delegation_depth": result.delegation_depth,
        "execution_time_ms": result.execution_time_ms,
        "blackboard_entry_id": result.blackboard_entry_id,
        "sub_session_id": result.sub_session_id,
    }


# --------------------------------------------------------------------- #
# 注册内置执行器
# --------------------------------------------------------------------- #

def _register_builtin_executors() -> None:
    register_node_executor("start", _execute_start)
    register_node_executor("end", _execute_end)
    register_node_executor("llm_call", _execute_llm_call)
    register_node_executor("tool_call", _execute_tool_call)
    register_node_executor("condition", _execute_condition)
    register_node_executor("parallel", _execute_parallel)
    register_node_executor("loop", _execute_loop)
    register_node_executor("output", _execute_output)
    register_node_executor("plan_step", _execute_plan_step)
    register_node_executor("agent_call", _execute_agent_call)


_register_builtin_executors()
