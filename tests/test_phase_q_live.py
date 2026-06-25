#!/usr/bin/env python3
"""Phase Q live 离线单测 — orchestrator checkpoint 检查逻辑（mock httpx）。"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from eval.phase_q_live import run_phase_q_live


class TestPhaseQLiveOrchestrator(unittest.IsolatedAsyncioTestCase):
    async def test_orchestrator_checkpoint_live_passes(self) -> None:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        export_resp = MagicMock(status_code=200, headers={"content-type": "text/yaml"}, text="plan_to_workflow s1")
        exec_resp = MagicMock(
            status_code=200,
            content=b'{"execution_id":"e1","status":"completed"}',
        )
        exec_resp.json.return_value = {"execution_id": "e1", "status": "completed"}
        cp_resp = MagicMock(status_code=200, content=b'{"status":"completed"}')
        cp_resp.json.return_value = {"status": "completed"}

        client.post = AsyncMock(side_effect=[export_resp, exec_resp])
        client.get = AsyncMock(return_value=cp_resp)

        with patch("eval.phase_q_live.httpx.AsyncClient", return_value=client):
            with patch.dict("os.environ", {"LLM_API_KEY": ""}, clear=False):
                checks = await run_phase_q_live()

        names = {c.name: c for c in checks}
        self.assertTrue(names["phase_q_orchestrator_checkpoint_live"].passed)
        self.assertTrue(names["phase_q_plan_export_live"].passed)


if __name__ == "__main__":
    unittest.main()
