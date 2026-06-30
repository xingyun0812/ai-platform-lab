#!/usr/bin/env python3
"""tests/test_orchestrator_traversal.py — 共享 traverse_workflow (#162 PR-3)。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.orchestrator.engine import (  # noqa: E402
    ExecutionContext,
    traverse_workflow,
)
from packages.agent.orchestrator.graph import GraphEdge, GraphNode, Workflow  # noqa: E402


def _linear_workflow() -> Workflow:
    return Workflow(
        workflow_id="trav-linear",
        name="linear",
        nodes=[
            GraphNode(node_id="start", node_type="start"),
            GraphNode(
                node_id="out1",
                node_type="output",
                config={"value": "hello"},
            ),
            GraphNode(node_id="end", node_type="end"),
        ],
        edges=[
            GraphEdge(from_node="start", to_node="out1"),
            GraphEdge(from_node="out1", to_node="end"),
        ],
        start_node="start",
        end_node="end",
    )


class TestTraverseWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_traverse_without_persister(self) -> None:
        wf = _linear_workflow()
        ctx = ExecutionContext()
        outcome = await traverse_workflow(
            wf,
            ctx,
            current=wf.start_node,
            steps=0,
            max_steps=10,
            timeout_seconds=30.0,
            start_time=__import__("time").time(),
        )
        self.assertIsNone(outcome.early_result)
        self.assertEqual(outcome.ctx.outputs["out1"]["value"], "hello")
        self.assertEqual(len(outcome.ctx.trace), 3)

    async def test_persister_receives_advance_and_complete(self) -> None:
        wf = _linear_workflow()
        ctx = ExecutionContext()
        persister = MagicMock()
        persister.after_advance = AsyncMock()
        persister.on_workflow_completed = AsyncMock(return_value=None)
        persister.on_node_failure_persist = AsyncMock()
        persister.after_error_redirect = AsyncMock()

        await traverse_workflow(
            wf,
            ctx,
            current=wf.start_node,
            steps=0,
            max_steps=10,
            timeout_seconds=30.0,
            start_time=__import__("time").time(),
            persister=persister,
        )

        persister.after_advance.assert_awaited()
        persister.on_workflow_completed.assert_awaited_once()


class TestCheckpointUsesTraversal(unittest.IsolatedAsyncioTestCase):
    async def test_checkpointed_execute_completed(self) -> None:
        from packages.agent.orchestrator.checkpoint_engine import execute_workflow_checkpointed

        wf = _linear_workflow()
        with patch(
            "packages.agent.orchestrator.checkpoint_engine.traverse_workflow",
            new=AsyncMock(
                return_value=MagicMock(
                    early_result=None,
                    ctx=ExecutionContext(
                        outputs={"out1": {"value": "hello"}},
                        trace=[{"node_id": "start", "status": "completed"}],
                    ),
                    last_output={"status": "completed"},
                    steps=3,
                )
            ),
        ) as mock_traverse:
            result = await execute_workflow_checkpointed(
                wf,
                tenant_id="admin",
                inputs={},
            )
        mock_traverse.assert_awaited_once()
        self.assertEqual(result.status, "completed")


if __name__ == "__main__":
    unittest.main()
