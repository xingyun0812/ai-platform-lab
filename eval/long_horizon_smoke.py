"""eval/long_horizon_smoke.py — Phase R R2 长程任务 smoke 测试。

场景：模拟跨 session 任务（断点续跑 2 次）。
"""

from __future__ import annotations

import importlib.util
import sys
import time
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _ensure_namespace(name: str) -> types.ModuleType:
    if name not in sys.modules:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    return sys.modules[name]


def _load_module(name: str, path: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Bootstrap namespace packages
_ensure_namespace("packages")
_ensure_namespace("packages.contracts")
_ensure_namespace("packages.agent")

# Stub contracts.errors to avoid Python 3.9 incompatibility
_errors_mod = types.ModuleType("packages.contracts.errors")


class _ErrorDetail:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def model_dump(self):
        return self.__dict__


class _ErrorBody:
    def __init__(self, error=None):
        self.error = error

    def model_dump(self):
        return {"error": self.error.model_dump() if self.error else None}


_errors_mod.ErrorDetail = _ErrorDetail  # type: ignore[attr-defined]
_errors_mod.ErrorBody = _ErrorBody  # type: ignore[attr-defined]
sys.modules["packages.contracts.errors"] = _errors_mod

_agent_schemas = _load_module(
    "packages.contracts.agent_schemas",
    str(REPO_ROOT / "packages" / "contracts" / "agent_schemas.py"),
)
_long_horizon = _load_module(
    "packages.agent.long_horizon",
    str(REPO_ROOT / "packages" / "agent" / "long_horizon.py"),
)

AgentPlan = _agent_schemas.AgentPlan
PlanStep = _agent_schemas.PlanStep

cancel_task = _long_horizon.cancel_task
checkpoint_task = _long_horizon.checkpoint_task
create_long_run = _long_horizon.create_long_run
get_long_run = _long_horizon.get_long_run
get_long_run_store = _long_horizon.get_long_run_store
get_task_status = _long_horizon.get_task_status
reset_long_run_store_for_tests = _long_horizon.reset_long_run_store_for_tests
resume_task = _long_horizon.resume_task


def _make_plan() -> AgentPlan:
    """3 步线性 plan：s1 → s2 → s3。"""
    return AgentPlan(
        goal="跨 session 长程任务 smoke",
        steps=[
            PlanStep(id="s1", description="收集数据", depends_on=[]),
            PlanStep(id="s2", description="分析数据", depends_on=["s1"]),
            PlanStep(id="s3", description="生成报告", depends_on=["s2"]),
        ],
    )


def run_smoke() -> None:
    reset_long_run_store_for_tests()
    store = get_long_run_store()
    plan = _make_plan()

    # -----------------------------------------------------------------------
    # Session 1: 创建任务，完成 s1 → checkpoint → 暂停
    # -----------------------------------------------------------------------
    print("[Session 1] 创建长程任务...")
    task = create_long_run(plan, tenant_id="tenant_smoke", session_id="session_001")
    task_id = task.task_id

    assert task.status == "pending", f"期望 pending, 得到 {task.status}"
    assert len(task.step_states) == 3

    # 模拟 s1 完成
    store.update_status(task_id, "running")
    task.step_states[0].status = "completed"
    task.step_states[0].completed_at = time.time()
    store.update_step_states(task_id, task.step_states)

    cp1 = checkpoint_task(task_id)
    assert cp1 is not None, "checkpoint_task 应返回 Checkpoint"
    assert cp1.task_id == task_id
    assert cp1.layer_index == 1, f"期望 layer_index=1, 得到 {cp1.layer_index}"

    # 暂停
    store.update_status(task_id, "paused")
    t = get_long_run(task_id)
    assert t is not None and t.status == "paused"

    print(f"  [Session 1] s1 完成，checkpoint {cp1.checkpoint_id[:8]}... 已创建，任务暂停")

    # -----------------------------------------------------------------------
    # Session 2: resume → 完成 s2 → checkpoint → 暂停
    # -----------------------------------------------------------------------
    print("[Session 2] 续跑任务...")
    resumed = resume_task(task_id)
    assert resumed is not None
    assert resumed.status == "running"

    # 验证从 checkpoint 恢复 —— s1 应仍为 completed
    assert resumed.step_states[0].status == "completed", "resume 后 s1 应仍为 completed"
    assert resumed.step_states[1].status == "pending", "resume 后 s2 应为 pending"

    # 模拟 s2 完成
    resumed.step_states[1].status = "completed"
    resumed.step_states[1].completed_at = time.time()
    store.update_step_states(task_id, resumed.step_states)

    cp2 = checkpoint_task(task_id)
    assert cp2 is not None
    assert cp2.layer_index == 2, f"期望 layer_index=2, 得到 {cp2.layer_index}"
    assert cp2.checkpoint_id != cp1.checkpoint_id

    store.update_status(task_id, "paused")
    print(f"  [Session 2] s2 完成，checkpoint {cp2.checkpoint_id[:8]}... 已创建，任务暂停")

    # -----------------------------------------------------------------------
    # Session 3: resume → 完成 s3 → 任务完成
    # -----------------------------------------------------------------------
    print("[Session 3] 最终续跑...")
    resumed3 = resume_task(task_id)
    assert resumed3 is not None
    assert resumed3.status == "running"

    # 从最新 checkpoint 恢复 —— s1 + s2 均为 completed
    assert resumed3.step_states[0].status == "completed"
    assert resumed3.step_states[1].status == "completed"
    assert resumed3.step_states[2].status == "pending"

    # 模拟 s3 完成
    resumed3.step_states[2].status = "completed"
    resumed3.step_states[2].completed_at = time.time()
    store.update_step_states(task_id, resumed3.step_states)

    # 最终 checkpoint（3 次 checkpoint 总计）
    cp3 = checkpoint_task(task_id)
    assert cp3 is not None
    assert cp3.layer_index == 3

    store.update_status(task_id, "completed")
    store.set_final_result(task_id, {"report": "final_report.pdf"})

    # 验证任务最终状态
    final = get_task_status(task_id)
    assert final is not None
    assert final["status"] == "completed"
    assert final["progress"]["completed"] == 3
    assert final["progress"]["percent"] == 100.0
    assert final["final_result"] == {"report": "final_report.pdf"}

    print(f"  [Session 3] 任务完成！进度: {final['progress']}")

    # -----------------------------------------------------------------------
    # 验证：checkpoint 数量 = 3（每次续跑后各一个）
    # -----------------------------------------------------------------------
    t_final = get_long_run(task_id)
    assert t_final is not None
    assert len(t_final.checkpoints) == 3, f"期望 3 个 checkpoint, 得到 {len(t_final.checkpoints)}"
    print(f"  总 checkpoint 数: {len(t_final.checkpoints)}")

    # -----------------------------------------------------------------------
    # 验证：无法取消已完成任务
    # -----------------------------------------------------------------------
    ok = cancel_task(task_id)
    assert not ok, "已完成的任务不应可取消"
    print("  已完成任务取消测试通过（拒绝取消）")

    print("\n✅ long_horizon smoke 全部通过！")


if __name__ == "__main__":
    run_smoke()
