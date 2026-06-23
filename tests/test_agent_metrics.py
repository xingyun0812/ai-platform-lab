"""Phase L #58 — Agent 四率指标单元测试（无需 LLM / Gateway）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.agent_run import (
    AgentCaseResult,
    TrajectoryMetrics,
    aggregate_trajectory_metrics,
    build_agent_metrics,
    compare_reports,
    evaluate_agent_case,
    validate_agent_baseline,
)


def _result(
    case_id: str,
    *,
    passed: bool = True,
    expect_tools: list[str] | None = None,
    direct_answer: bool = False,
    require_tools: bool = False,
    first_tool_correct: bool | None = None,
    needless: bool = False,
    missing: bool = False,
    arg_invalid: bool = False,
    tool_names: list[str] | None = None,
) -> AgentCaseResult:
    return AgentCaseResult(
        id=case_id,
        tenant_id="admin",
        passed=passed,
        reason="ok",
        http_status=200,
        expect_tools=expect_tools or [],
        direct_answer=direct_answer,
        require_tools=require_tools,
        tool_names=tool_names or [],
        trajectory=TrajectoryMetrics(
            first_tool_correct=first_tool_correct,
            needless_tool=needless,
            missing_tool=missing,
            arg_invalid=arg_invalid,
            tool_names=tool_names or [],
        ),
    )


def test_precision_at_1_in_expect_tools():
    case = {"expect_tools": ["calc"], "require_tools": True}
    ok, _, m = evaluate_agent_case(
        case,
        status=200,
        body={"tool_calls": [{"tool_name": "calc", "status": "ok"}], "final_message": "40"},
    )
    assert ok
    assert m.first_tool_correct is True


def test_precision_at_1_wrong_first_tool():
    case = {"expect_tools": ["calc"], "require_tools": True}
    ok, reason, m = evaluate_agent_case(
        case,
        status=200,
        body={
            "tool_calls": [{"tool_name": "get_kb_snippet", "status": "ok"}],
            "final_message": "x",
        },
    )
    assert not ok
    assert m.first_tool_correct is False
    assert "calc" in reason or "get_kb_snippet" in reason


def test_needless_tool_on_direct_answer():
    case = {"direct_answer": True}
    ok, _, m = evaluate_agent_case(
        case,
        status=200,
        body={"tool_calls": [{"tool_name": "calc"}], "final_message": "hi"},
    )
    assert not ok
    assert m.needless_tool is True


def test_direct_answer_no_tools_passes():
    case = {"direct_answer": True}
    ok, _, m = evaluate_agent_case(
        case,
        status=200,
        body={"tool_calls": [], "final_message": "你好"},
    )
    assert ok
    assert m.needless_tool is False


def test_missing_tool_when_require_tools():
    case = {"require_tools": True, "expect_tools": ["calc"]}
    ok, _, m = evaluate_agent_case(
        case,
        status=200,
        body={"tool_calls": [], "final_message": "42"},
    )
    assert not ok
    assert m.missing_tool is True


def test_arg_invalid_detects_error_code():
    case = {"require_tools": True, "expect_tools": ["calc"]}
    ok, _, m = evaluate_agent_case(
        case,
        status=200,
        body={
            "tool_calls": [{"tool_name": "calc", "status": "failed", "error": "AGENT_TOOL_BAD_ARGS: bad"}],
            "final_message": "",
        },
    )
    assert m.arg_invalid is True


def test_expect_error_code_path():
    case = {"expect": "error", "expect_error_code": "AGENT_TOOL_FORBIDDEN"}
    ok, _, _ = evaluate_agent_case(
        case,
        status=403,
        body={"error": {"code": "AGENT_TOOL_FORBIDDEN", "message": "forbidden"}},
    )
    assert ok


def test_aggregate_metrics_denominators():
    results = [
        _result("p1", expect_tools=["calc"], first_tool_correct=True, tool_names=["calc"]),
        _result("p2", expect_tools=["calc"], first_tool_correct=False, tool_names=["get_kb_snippet"]),
        _result("n1", direct_answer=True, needless=True, tool_names=["calc"]),
        _result("n2", direct_answer=True, needless=False),
        _result("m1", require_tools=True, missing=True),
        _result("m2", require_tools=True, missing=False, tool_names=["calc"]),
        _result("a1", tool_names=["calc"], arg_invalid=True),
        _result("a2", tool_names=["calc"], arg_invalid=False),
    ]
    summary = aggregate_trajectory_metrics(results)
    assert summary["tool_precision_at_1"] == 0.5
    assert summary["needless_tool_rate"] == 0.5
    assert summary["missing_tool_rate"] == 0.5
    assert summary["arg_valid_rate"] == 0.8333


def test_aggregate_empty_denominators():
    summary = aggregate_trajectory_metrics([])
    assert summary["tool_precision_at_1"] is None
    assert summary["needless_tool_rate"] is None
    assert summary["missing_tool_rate"] is None
    assert summary["arg_valid_rate"] is None


def test_build_agent_metrics_block():
    summary = aggregate_trajectory_metrics(
        [_result("x", expect_tools=["calc"], require_tools=True, first_tool_correct=True, tool_names=["calc"])]
    )
    metrics = build_agent_metrics(summary)
    assert set(metrics.keys()) == {
        "tool_precision_at_1",
        "needless_tool_rate",
        "missing_tool_rate",
        "arg_valid_rate",
    }


def test_compare_reports_agent_metrics(tmp_path: Path):
    a = {
        "run_id": "a",
        "summary": {"pass_rate": 0.8, "tool_precision_at_1": 0.9},
        "agent_metrics": {
            "tool_precision_at_1": 0.9,
            "needless_tool_rate": 0.1,
            "missing_tool_rate": 0.2,
            "arg_valid_rate": 1.0,
        },
        "results": [{"id": "c1", "passed": True}],
    }
    b = {
        "run_id": "b",
        "summary": {"pass_rate": 0.9, "tool_precision_at_1": 1.0},
        "agent_metrics": {
            "tool_precision_at_1": 1.0,
            "needless_tool_rate": 0.0,
            "missing_tool_rate": 0.1,
            "arg_valid_rate": 0.95,
        },
        "results": [{"id": "c1", "passed": True}],
    }
    pa = tmp_path / "a.json"
    pb = tmp_path / "b.json"
    pa.write_text(json.dumps(a), encoding="utf-8")
    pb.write_text(json.dumps(b), encoding="utf-8")
    diff = compare_reports(pa, pb)
    assert diff["pass_rate_delta"] == pytest.approx(0.1)
    assert diff["agent_metrics"]["tool_precision_at_1"]["delta"] == pytest.approx(0.1)


def test_validate_agent_baseline_ok():
    path = Path(__file__).resolve().parents[1] / "eval" / "agent_baseline.jsonl"
    ok, errors = validate_agent_baseline(path)
    assert ok, errors


def test_legacy_expect_no_tools_maps_to_direct_answer():
    case = {"expect_no_tools": True}
    ok, _, m = evaluate_agent_case(
        case,
        status=200,
        body={"tool_calls": [{"tool_name": "calc"}], "final_message": "x"},
    )
    assert not ok
    assert m.needless_tool is True
