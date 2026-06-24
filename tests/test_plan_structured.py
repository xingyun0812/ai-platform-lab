#!/usr/bin/env python3
"""tests/test_plan_structured.py — Q1 Structured plan output tests (Issue #116)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.planner import (  # noqa: E402
    PlannerError,
    build_response_format_schema,
    generate_plan,
    is_structured_mode,
)


def _run_async(coro):
    """Helper to run coroutines in tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _valid_plan_payload(**overrides) -> dict:
    base = {
        "goal": "测试目标",
        "steps": [
            {"id": "s1", "description": "第一步", "depends_on": []},
        ],
    }
    base.update(overrides)
    return base


def _make_route_response(body: dict):
    """Create a mock upstream route response."""

    class FakeRoute:
        status = 200
        error = None

    r = FakeRoute()
    r.body = body
    return r


def _content_body(payload: dict) -> dict:
    return {"choices": [{"message": {"content": json.dumps(payload)}}]}


class TestBuildResponseFormatSchema(unittest.TestCase):
    def test_build_response_format_schema_keys(self) -> None:
        schema = build_response_format_schema()
        self.assertEqual(schema["type"], "json_schema")
        js = schema["json_schema"]
        self.assertEqual(js["name"], "agent_plan")
        self.assertTrue(js["strict"])
        inner = js["schema"]
        self.assertIn("goal", inner["properties"])
        self.assertIn("steps", inner["properties"])
        self.assertFalse(inner["additionalProperties"])

    def test_schema_steps_item_required_fields(self) -> None:
        schema = build_response_format_schema()
        step_schema = schema["json_schema"]["schema"]["properties"]["steps"]["items"]
        required = step_schema["required"]
        self.assertIn("id", required)
        self.assertIn("description", required)
        self.assertIn("depends_on", required)
        self.assertFalse(step_schema["additionalProperties"])

    def test_schema_top_level_required(self) -> None:
        schema = build_response_format_schema()
        required = schema["json_schema"]["schema"]["required"]
        self.assertIn("goal", required)
        self.assertIn("steps", required)

    def test_schema_is_idempotent(self) -> None:
        s1 = build_response_format_schema()
        s2 = build_response_format_schema()
        self.assertEqual(s1, s2)


class TestIsStructuredMode(unittest.TestCase):
    def test_is_structured_mode_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLAN_OUTPUT_MODE", None)
            self.assertTrue(is_structured_mode())

    def test_is_structured_mode_env_legacy(self) -> None:
        with patch.dict(os.environ, {"PLAN_OUTPUT_MODE": "legacy"}):
            self.assertFalse(is_structured_mode())

    def test_is_structured_mode_env_structured(self) -> None:
        with patch.dict(os.environ, {"PLAN_OUTPUT_MODE": "structured"}):
            self.assertTrue(is_structured_mode())

    def test_is_structured_mode_case_insensitive(self) -> None:
        with patch.dict(os.environ, {"PLAN_OUTPUT_MODE": "STRUCTURED"}):
            self.assertTrue(is_structured_mode())
        with patch.dict(os.environ, {"PLAN_OUTPUT_MODE": "LEGACY"}):
            self.assertFalse(is_structured_mode())


class TestGeneratePlanStructured(unittest.TestCase):
    def test_generate_plan_structured_success(self) -> None:
        payload = _valid_plan_payload(
            steps=[
                {"id": "s1", "description": "step1", "depends_on": []},
                {"id": "s2", "description": "step2", "depends_on": ["s1"]},
            ]
        )
        captured_payloads: list[dict] = []

        async def fake_route(body):
            captured_payloads.append(body)
            return _make_route_response(_content_body(payload))

        async def _run():
            with patch.dict(os.environ, {"PLAN_OUTPUT_MODE": "structured"}):
                with patch("packages.agent.planner.forward_with_model_router", fake_route):
                    plan, model = await generate_plan(
                        goal="测试 structured 路径",
                        allowed_models=(),
                        allowed_tools=(),
                    )
            self.assertEqual(len(plan.steps), 2)
            self.assertEqual(plan.goal, payload["goal"])
            self.assertIn("response_format", captured_payloads[0])
            rf = captured_payloads[0]["response_format"]
            self.assertEqual(rf["type"], "json_schema")

        _run_async(_run())

    def test_generate_plan_structured_fallback_on_parse_error(self) -> None:
        valid_payload = _valid_plan_payload()
        call_count = [0]

        async def fake_route(body):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_route_response(
                    {"choices": [{"message": {"content": "not valid json {{{!"}}]}
                )
            return _make_route_response(_content_body(valid_payload))

        async def _run():
            with patch.dict(os.environ, {"PLAN_OUTPUT_MODE": "structured"}):
                with patch("packages.agent.planner.forward_with_model_router", fake_route):
                    plan, _ = await generate_plan(
                        goal="测试 fallback",
                        allowed_models=(),
                        allowed_tools=(),
                    )
            self.assertEqual(plan.goal, valid_payload["goal"])
            self.assertEqual(call_count[0], 2)

        _run_async(_run())

    def test_generate_plan_legacy_mode(self) -> None:
        payload = _valid_plan_payload()
        captured_payloads: list[dict] = []

        async def fake_route(body):
            captured_payloads.append(body)
            return _make_route_response(_content_body(payload))

        async def _run():
            with patch.dict(os.environ, {"PLAN_OUTPUT_MODE": "legacy"}):
                with patch("packages.agent.planner.forward_with_model_router", fake_route):
                    plan, _ = await generate_plan(
                        goal="legacy 模式测试",
                        allowed_models=(),
                        allowed_tools=(),
                    )
            self.assertEqual(plan.goal, payload["goal"])
            self.assertEqual(len(captured_payloads), 1)
            self.assertNotIn("response_format", captured_payloads[0])

        _run_async(_run())

    def test_generate_plan_structured_validates_plan(self) -> None:
        bad_payload = {"goal": "只有 goal，没有 steps"}
        call_count = [0]

        async def fake_route(body):
            call_count[0] += 1
            return _make_route_response(_content_body(bad_payload))

        async def _run():
            with patch.dict(os.environ, {"PLAN_OUTPUT_MODE": "structured"}):
                with patch("packages.agent.planner.forward_with_model_router", fake_route):
                    with self.assertRaises(PlannerError) as ctx:
                        await generate_plan(
                            goal="validation test",
                            allowed_models=(),
                            allowed_tools=(),
                        )
            self.assertIn(ctx.exception.code, ("PLAN_INVALID", "PLAN_PARSE_ERROR"))

        _run_async(_run())

    def test_generate_plan_structured_fallback_metrics(self) -> None:
        valid_payload = _valid_plan_payload()
        call_count = [0]

        async def fake_route(body):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_route_response(
                    {"choices": [{"message": {"content": "INVALID JSON %%"}}]}
                )
            return _make_route_response(_content_body(valid_payload))

        async def _run():
            import packages.agent.planner as planner_mod

            with patch.dict(os.environ, {"PLAN_OUTPUT_MODE": "structured"}):
                with patch("packages.agent.planner.forward_with_model_router", fake_route):
                    with patch.object(planner_mod.logger, "warning") as mock_warn:
                        plan, _ = await generate_plan(
                            goal="metrics fallback test",
                            allowed_models=(),
                            allowed_tools=(),
                        )
                        mock_warn.assert_called_once()
                        call_args = mock_warn.call_args[0][0]
                        self.assertIn("fallback", call_args)
            self.assertEqual(plan.goal, valid_payload["goal"])

        _run_async(_run())

    def test_generate_plan_upstream_error_propagates(self) -> None:
        async def fake_route(body):
            class ErrRoute:
                status = 500
                body = None
                error = "internal server error"

            return ErrRoute()

        async def _run():
            with patch.dict(os.environ, {"PLAN_OUTPUT_MODE": "legacy"}):
                with patch("packages.agent.planner.forward_with_model_router", fake_route):
                    with self.assertRaises(PlannerError) as ctx:
                        await generate_plan(
                            goal="upstream error test",
                            allowed_models=(),
                            allowed_tools=(),
                        )
            self.assertEqual(ctx.exception.code, "PLAN_UPSTREAM_ERROR")

        _run_async(_run())

    def test_generate_plan_structured_response_format_schema_correct(self) -> None:
        captured: list[dict] = []

        async def fake_route(body):
            captured.append(body)
            return _make_route_response(_content_body(_valid_plan_payload()))

        async def _run():
            with patch.dict(os.environ, {"PLAN_OUTPUT_MODE": "structured"}):
                with patch("packages.agent.planner.forward_with_model_router", fake_route):
                    await generate_plan(
                        goal="schema shape test",
                        allowed_models=(),
                        allowed_tools=(),
                    )

            rf = captured[0]["response_format"]
            self.assertEqual(rf["type"], "json_schema")
            self.assertEqual(rf["json_schema"]["name"], "agent_plan")
            self.assertTrue(rf["json_schema"]["strict"])
            schema = rf["json_schema"]["schema"]
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])

        _run_async(_run())


if __name__ == "__main__":
    unittest.main()
