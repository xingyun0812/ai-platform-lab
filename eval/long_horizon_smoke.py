"""eval/long_horizon_smoke.py — Phase R R2 长程任务 smoke 测试。

场景：模拟跨 session 任务（断点续跑 2 次）。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Normal package imports — the real packages must stay intact (the previous
# bootstrap registered fake empty namespace modules, breaking the
# packages.agent → packages.contracts chain under import).
from packages.agent.long_horizon import (  # noqa: E402
    cancel_task,
    checkpoint_task,
    create_long_run,
    get_long_run,
    get_long_run_store,
    get_task_status,
    reset_long_run_store_for_tests,
    resume_task,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402


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


async def run_smoke() -> None:
    reset_long_run_store_for_tests()
    store = get_long_run_store()
    plan = _make_plan()

    print("[Session 1] 创建长程任务...")
    task = await create_long_run(plan, tenant_id="tenant_smoke", session_id="session_001")
    task_id = task.task_id

    assert task.status == "pending", f"期望 pending, 得到 {task.status}"
    assert len(task.step_states) == 3

    await store.update_status(task_id, "running")
    task.step_states[0].status = "completed"
    task.step_states[0].completed_at = time.time()
    await store.update_step_states(task_id, task.step_states)

    cp1 = await checkpoint_task(task_id)
    assert cp1 is not None, "checkpoint_task 应返回 Checkpoint"
    assert cp1.task_id == task_id
    assert cp1.layer_index == 1, f"期望 layer_index=1, 得到 {cp1.layer_index}"

    await store.update_status(task_id, "paused")
    t = await get_long_run(task_id)
    assert t is not None and t.status == "paused"

    print(f"  [Session 1] s1 完成，checkpoint {cp1.checkpoint_id[:8]}... 已创建，任务暂停")

    print("[Session 2] 续跑任务...")
    resumed = await resume_task(task_id)
    assert resumed is not None
    assert resumed.status == "running"
    assert resumed.step_states[0].status == "completed", "resume 后 s1 应仍为 completed"
    assert resumed.step_states[1].status == "pending", "resume 后 s2 应为 pending"

    resumed.step_states[1].status = "completed"
    resumed.step_states[1].completed_at = time.time()
    await store.update_step_states(task_id, resumed.step_states)

    cp2 = await checkpoint_task(task_id)
    assert cp2 is not None
    assert cp2.layer_index == 2, f"期望 layer_index=2, 得到 {cp2.layer_index}"
    assert cp2.checkpoint_id != cp1.checkpoint_id

    await store.update_status(task_id, "paused")
    print(f"  [Session 2] s2 完成，checkpoint {cp2.checkpoint_id[:8]}... 已创建，任务暂停")

    print("[Session 3] 最终续跑...")
    resumed3 = await resume_task(task_id)
    assert resumed3 is not None
    assert resumed3.status == "running"
    assert resumed3.step_states[0].status == "completed"
    assert resumed3.step_states[1].status == "completed"
    assert resumed3.step_states[2].status == "pending"

    resumed3.step_states[2].status = "completed"
    resumed3.step_states[2].completed_at = time.time()
    await store.update_step_states(task_id, resumed3.step_states)

    cp3 = await checkpoint_task(task_id)
    assert cp3 is not None
    assert cp3.layer_index == 3

    await store.update_status(task_id, "completed")
    await store.set_final_result(task_id, {"report": "final_report.pdf"})

    final = await get_task_status(task_id)
    assert final is not None
    assert final["status"] == "completed"
    assert final["progress"]["completed"] == 3
    assert final["progress"]["percent"] == 100.0
    assert final["final_result"] == {"report": "final_report.pdf"}

    print(f"  [Session 3] 任务完成！进度: {final['progress']}")

    t_final = await get_long_run(task_id)
    assert t_final is not None
    assert len(t_final.checkpoints) == 3, f"期望 3 个 checkpoint, 得到 {len(t_final.checkpoints)}"
    print(f"  总 checkpoint 数: {len(t_final.checkpoints)}")

    ok = await cancel_task(task_id)
    assert not ok, "已完成的任务不应可取消"
    print("  已完成任务取消测试通过（拒绝取消）")

    print("\n✅ long_horizon smoke 全部通过！")


if __name__ == "__main__":
    asyncio.run(run_smoke())
