"""Phase L #60 — Agent eval gate 单元测试。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from eval.agent_gate import (
    AgentGateResult,
    check_agent_gate,
    run_offline_gate,
    validate_agent_scenarios,
)

REPO = Path(__file__).resolve().parents[1]


def test_validate_agent_scenarios_ok():
    ok, errors = validate_agent_scenarios(REPO / "eval" / "baselines" / "agent_scenarios.jsonl")
    assert ok, errors


def test_validate_agent_scenarios_too_few(tmp_path: Path):
    p = tmp_path / "few.jsonl"
    p.write_text('{"id": "a", "require_tools": true, "direct_answer": false}\n', encoding="utf-8")
    ok, errors = validate_agent_scenarios(p)
    assert not ok
    assert any("30" in e for e in errors)


def test_gate_pass_improvement_2pp():
    baseline = {"pass_rate": 0.80, "agent_metrics": {"tool_precision_at_1": 0.9}}
    current = {"pass_rate": 0.82, "agent_metrics": {"tool_precision_at_1": 0.92}}
    gate = check_agent_gate(current, baseline, threshold_pct=5.0)
    assert gate.passed
    assert gate.pass_rate_delta_pp == pytest.approx(2.0)


def test_gate_fail_regression_6pp():
    baseline = {"pass_rate": 0.80, "agent_metrics": {}}
    current = {"pass_rate": 0.74, "agent_metrics": {}}
    gate = check_agent_gate(current, baseline, threshold_pct=5.0)
    assert not gate.passed
    assert gate.pass_rate_delta_pp == pytest.approx(-6.0)


def test_gate_fail_exactly_at_threshold():
    baseline = {"pass_rate": 0.80, "agent_metrics": {}}
    current = {"pass_rate": 0.75, "agent_metrics": {}}
    gate = check_agent_gate(current, baseline, threshold_pct=5.0)
    assert not gate.passed


def test_gate_pass_within_threshold():
    baseline = {"pass_rate": 0.80, "agent_metrics": {}}
    current = {"pass_rate": 0.76, "agent_metrics": {}}
    gate = check_agent_gate(current, baseline, threshold_pct=5.0)
    assert gate.passed


def test_metric_deltas_computed():
    baseline = {
        "pass_rate": 0.8,
        "agent_metrics": {"tool_precision_at_1": 0.9, "arg_valid_rate": 1.0},
    }
    current = {
        "pass_rate": 0.85,
        "agent_metrics": {"tool_precision_at_1": 0.95, "arg_valid_rate": 0.98},
    }
    gate = check_agent_gate(current, baseline)
    assert gate.metric_deltas_pp["tool_precision_at_1"] == pytest.approx(5.0)
    assert gate.metric_deltas_pp["arg_valid_rate"] == pytest.approx(-2.0)


def test_run_offline_gate_passes_on_main():
    gate = run_offline_gate()
    assert isinstance(gate, AgentGateResult)
    assert gate.passed


def test_check_command_with_report_files(tmp_path: Path):
    from eval.agent_gate import load_report_summary, check_agent_gate

    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(
        json.dumps({"summary": {"pass_rate": 0.9}, "agent_metrics": {"tool_precision_at_1": 1.0}}),
        encoding="utf-8",
    )
    b.write_text(json.dumps({"pass_rate": 0.85, "agent_metrics": {"tool_precision_at_1": 0.95}}), encoding="utf-8")
    gate = check_agent_gate(load_report_summary(a), load_report_summary(b))
    assert gate.passed
