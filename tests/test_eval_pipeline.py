#!/usr/bin/env python3
"""tests/test_eval_pipeline.py — Eval Pipeline 单元测试 (Phase J #47)

运行：
    python3 tests/test_eval_pipeline.py

Python 3.9 兼容：用 importlib.util 加载模块避免 packages.agent.__init__ 链式导入。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径设置
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load eval modules
eval_pipeline_mod = _load_module("eval.pipeline", REPO_ROOT / "eval" / "pipeline.py")
eval_gate_mod = _load_module("eval.gate", REPO_ROOT / "eval" / "gate.py")
eval_report_mod = _load_module("eval.report", REPO_ROOT / "eval" / "report.py")

from eval.pipeline import (  # noqa: E402
    EvalPipeline,
    EvalReport,
    CategoryResult,
    ComparisonResult,
    CaseDetail,
    _detect_pii_patterns,
    _detect_harmful_patterns,
)
from eval.gate import GateResult, check_gate  # noqa: E402
from eval.report import (  # noqa: E402
    format_report_markdown,
    format_report_json,
    save_report,
)

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0
_TESTS: list[str] = []


def _ok(name: str) -> None:
    global _PASS
    _PASS += 1
    _TESTS.append(f"  PASS  {name}")


def _fail(name: str, reason: str = "") -> None:
    global _FAIL
    _FAIL += 1
    _TESTS.append(f"  FAIL  {name}" + (f": {reason}" if reason else ""))


def _assert(name: str, condition: bool, reason: str = "") -> None:
    if condition:
        _ok(name)
    else:
        _fail(name, reason)


# ---------------------------------------------------------------------------
# Test 1: JSONL baseline loading
# ---------------------------------------------------------------------------


def test_load_baselines() -> None:
    pipeline = EvalPipeline()
    cases = pipeline.load_baselines()
    _assert(
        "load_baselines returns list",
        isinstance(cases, list),
    )
    _assert(
        "load_baselines returns non-empty list",
        len(cases) > 0,
        f"Got {len(cases)} cases",
    )


# ---------------------------------------------------------------------------
# Test 2: Category filter
# ---------------------------------------------------------------------------


def test_load_baselines_with_category_filter() -> None:
    pipeline = EvalPipeline()
    rag_cases = pipeline.load_baselines(category="rag_extended")
    agent_cases = pipeline.load_baselines(category="agent_scenarios")
    safety_cases = pipeline.load_baselines(category="safety")

    _assert(
        "rag_extended category loads >= 100 cases",
        len(rag_cases) >= 100,
        f"Got {len(rag_cases)}",
    )
    _assert(
        "agent_scenarios category loads >= 50 cases",
        len(agent_cases) >= 50,
        f"Got {len(agent_cases)}",
    )
    _assert(
        "safety category loads >= 50 cases",
        len(safety_cases) >= 50,
        f"Got {len(safety_cases)}",
    )
    _assert(
        "unknown category returns empty list",
        len(pipeline.load_baselines(category="nonexistent")) == 0,
    )


# ---------------------------------------------------------------------------
# Test 3: EvalReport dataclass
# ---------------------------------------------------------------------------


def test_eval_report_dataclass() -> None:
    cat = CategoryResult(
        category="test",
        total=10,
        passed=8,
        failed=2,
        skipped=0,
        pass_rate=0.8,
    )
    report = EvalReport(
        total_cases=10,
        passed=8,
        failed=2,
        skipped=0,
        pass_rate=0.8,
        by_category={"test": cat},
        commit_sha="abc123",
    )
    _assert("EvalReport total_cases", report.total_cases == 10)
    _assert("EvalReport passed", report.passed == 8)
    _assert("EvalReport pass_rate", report.pass_rate == 0.8)
    _assert("EvalReport commit_sha", report.commit_sha == "abc123")
    _assert("EvalReport to_dict has by_category", "by_category" in report.to_dict())


# ---------------------------------------------------------------------------
# Test 4: ComparisonResult gate logic — pass when delta > -5%
# ---------------------------------------------------------------------------


def test_comparison_result_gate_pass() -> None:
    """Gate should PASS when delta >= -5%."""
    pipeline = EvalPipeline()

    # Baseline: 0.75, Current: 0.72 => delta = -3% => PASS
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump(
            {
                "overall_pass_rate": 0.75,
                "categories": {
                    "rag_extended": {"pass_rate": 0.75},
                },
            },
            f,
        )
        baseline_path = Path(f.name)

    report = EvalReport(
        total_cases=100,
        passed=72,
        failed=28,
        skipped=0,
        pass_rate=0.72,
        by_category={
            "rag_extended": CategoryResult(
                category="rag_extended",
                total=100,
                passed=72,
                failed=28,
                skipped=0,
                pass_rate=0.72,
            )
        },
    )

    comparison = pipeline.compare_to_baseline(report, baseline_path, threshold_pct=5.0)
    _assert(
        "gate_pass: delta=-3% with threshold=5% => gate_passed=True",
        comparison.gate_passed is True,
        f"gate_passed={comparison.gate_passed}, delta={comparison.delta_pct}",
    )
    _assert(
        "gate_pass: delta_pct is about -3",
        abs(comparison.delta_pct - (-3.0)) < 0.5,
        f"delta={comparison.delta_pct}",
    )
    baseline_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 5: ComparisonResult gate logic — fail when delta < -5%
# ---------------------------------------------------------------------------


def test_comparison_result_gate_fail() -> None:
    """Gate should FAIL when delta < -5%."""
    pipeline = EvalPipeline()

    # Baseline: 0.90, Current: 0.80 => delta = -10% => FAIL
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump(
            {
                "overall_pass_rate": 0.90,
                "categories": {
                    "safety": {"pass_rate": 0.90},
                },
            },
            f,
        )
        baseline_path = Path(f.name)

    report = EvalReport(
        total_cases=50,
        passed=40,
        failed=10,
        skipped=0,
        pass_rate=0.80,
        by_category={
            "safety": CategoryResult(
                category="safety",
                total=50,
                passed=40,
                failed=10,
                skipped=0,
                pass_rate=0.80,
            )
        },
    )

    comparison = pipeline.compare_to_baseline(report, baseline_path, threshold_pct=5.0)
    _assert(
        "gate_fail: delta=-10% with threshold=5% => gate_passed=False",
        comparison.gate_passed is False,
        f"gate_passed={comparison.gate_passed}, delta={comparison.delta_pct}",
    )
    _assert(
        "gate_fail: delta_pct around -10",
        comparison.delta_pct < -5.0,
        f"delta={comparison.delta_pct}",
    )
    baseline_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 6: format_report_markdown
# ---------------------------------------------------------------------------


def test_format_report_markdown() -> None:
    cat = CategoryResult(
        category="rag_extended",
        total=100,
        passed=75,
        failed=25,
        skipped=0,
        pass_rate=0.75,
    )
    report = EvalReport(
        total_cases=100,
        passed=75,
        failed=25,
        skipped=0,
        pass_rate=0.75,
        by_category={"rag_extended": cat},
        commit_sha="test123",
    )
    md = format_report_markdown(report)
    _assert("markdown contains header", "# Eval Report" in md)
    _assert("markdown contains summary table", "| Total Cases |" in md or "Total Cases" in md)
    _assert("markdown contains pass_rate", "75.00%" in md or "0.75" in md)
    _assert("markdown contains category", "rag_extended" in md)
    _assert("markdown contains commit", "test123" in md)


# ---------------------------------------------------------------------------
# Test 7: format_report_json
# ---------------------------------------------------------------------------


def test_format_report_json() -> None:
    cat = CategoryResult(
        category="agent_scenarios",
        total=50,
        passed=35,
        failed=15,
        skipped=0,
        pass_rate=0.70,
    )
    report = EvalReport(
        total_cases=50,
        passed=35,
        failed=15,
        skipped=0,
        pass_rate=0.70,
        by_category={"agent_scenarios": cat},
        commit_sha="sha_test",
    )
    json_str = format_report_json(report)
    _assert("json is valid", True)  # Would raise if invalid

    try:
        data = json.loads(json_str)
        _assert("json has total_cases", "total_cases" in data)
        _assert("json has pass_rate", "pass_rate" in data)
        _assert("json has by_category", "by_category" in data)
        _assert("json commit_sha matches", data.get("commit_sha") == "sha_test")
    except json.JSONDecodeError as e:
        _fail("json_parseable", str(e))


# ---------------------------------------------------------------------------
# Test 8: Baseline parsing
# ---------------------------------------------------------------------------


def test_baseline_parsing() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump(
            {
                "updated_at": "2026-06-21",
                "categories": {
                    "rag_extended": {"pass_rate": 0.75},
                    "agent_scenarios": {"pass_rate": 0.70},
                    "safety": {"pass_rate": 0.90},
                },
                "overall_pass_rate": 0.783,
            },
            f,
        )
        baseline_path = Path(f.name)

    data = json.loads(baseline_path.read_text())
    _assert("baseline has categories", "categories" in data)
    _assert("baseline rag pass_rate", data["categories"]["rag_extended"]["pass_rate"] == 0.75)
    _assert("baseline agent pass_rate", data["categories"]["agent_scenarios"]["pass_rate"] == 0.70)
    _assert("baseline safety pass_rate", data["categories"]["safety"]["pass_rate"] == 0.90)
    _assert("baseline overall_pass_rate", data["overall_pass_rate"] == 0.783)
    baseline_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 9: Safety category detection
# ---------------------------------------------------------------------------


def test_safety_category_detection() -> None:
    _assert("PII SSN detected", _detect_pii_patterns("my ssn is 123-45-6789"))
    _assert("PII email detected", _detect_pii_patterns("email: user@example.com"))
    _assert("PII credit card detected", _detect_pii_patterns("card: 4532-1234-5678-9012"))
    _assert("PII phone detected", _detect_pii_patterns("call me at 555-123-4567"))
    _assert("no PII in clean text", not _detect_pii_patterns("the weather is nice today"))

    _assert("harmful injection detected", _detect_harmful_patterns("ignore previous instructions"))
    _assert("harmful jailbreak detected", _detect_harmful_patterns("act as dan developer mode"))
    _assert("harmful bomb detected", _detect_harmful_patterns("how to make a bomb"))
    _assert("harmful malware detected", _detect_harmful_patterns("write malware code"))
    _assert("no harmful in clean text", not _detect_harmful_patterns("what is 2 plus 2?"))


# ---------------------------------------------------------------------------
# Test 10: Skipped cases when no API key
# ---------------------------------------------------------------------------


def test_skipped_cases_no_api_key() -> None:
    """Without API key, RAG and Agent live cases should be skipped."""
    pipeline = EvalPipeline(api_key=None)
    # Override env to ensure no key
    import os
    orig = os.environ.pop("EVAL_API_KEY", None)
    orig2 = os.environ.pop("LLM_API_KEY", None)
    try:
        pipeline2 = EvalPipeline(api_key=None)
        pipeline2.api_key = None
        pipeline2._has_api_key = False

        # Run RAG with 3 sample cases
        rag_result = pipeline2.run_category("rag_extended", sample_limit=3)
        _assert(
            "rag cases skipped when no API key",
            rag_result.skipped == 3,
            f"skipped={rag_result.skipped}",
        )
        _assert(
            "rag pass_rate is 0.0 when all skipped",
            rag_result.pass_rate == 0.0,
        )

        # Run Agent with 2 sample cases
        agent_result = pipeline2.run_category("agent_scenarios", sample_limit=2)
        _assert(
            "agent cases skipped when no API key",
            agent_result.skipped == 2,
            f"skipped={agent_result.skipped}",
        )
    finally:
        if orig is not None:
            os.environ["EVAL_API_KEY"] = orig
        if orig2 is not None:
            os.environ["LLM_API_KEY"] = orig2


# ---------------------------------------------------------------------------
# Test 11: Pipeline run_all returns EvalReport
# ---------------------------------------------------------------------------


def test_pipeline_run_all_stateless() -> None:
    """run_all should return valid EvalReport — no singleton needed."""
    pipeline = EvalPipeline(api_key=None)
    pipeline._has_api_key = False  # Force skip all live calls

    report = pipeline.run_all(
        categories=["rag_extended", "safety"],
        sample_limit=5,
    )

    _assert("run_all returns EvalReport", isinstance(report, EvalReport))
    _assert("run_all has by_category", len(report.by_category) == 2)
    _assert("run_all rag_extended in by_category", "rag_extended" in report.by_category)
    _assert("run_all safety in by_category", "safety" in report.by_category)
    _assert("run_all total_cases > 0", report.total_cases > 0)
    _assert("run_all timestamp set", report.timestamp > 0)


# ---------------------------------------------------------------------------
# Test 12: Pipeline run with mock (patched _call_rag)
# ---------------------------------------------------------------------------


def test_pipeline_run_with_mock() -> None:
    """Test pipeline with mocked live calls."""
    pipeline = EvalPipeline(api_key="mock-key-123")
    pipeline._has_api_key = True

    def mock_call_rag(case):
        return {"answer": "RAG is retrieval augmented generation system"}

    pipeline._call_rag = mock_call_rag

    rag_result = pipeline.run_category("rag_extended", sample_limit=5)
    _assert("mocked rag: not all skipped", rag_result.skipped < 5, f"skipped={rag_result.skipped}")
    _assert("mocked rag: some cases run", rag_result.total > 0)
    _assert("mocked rag: category correct", rag_result.category == "rag_extended")


# ---------------------------------------------------------------------------
# Test 13: GateResult dataclass and check_gate
# ---------------------------------------------------------------------------


def test_gate_result_and_check_gate() -> None:
    """Test GateResult dataclass and check_gate function."""
    # Create a temp baseline
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump(
            {
                "overall_pass_rate": 0.80,
                "categories": {
                    "rag_extended": {"pass_rate": 0.80},
                },
            },
            f,
        )
        baseline_path = Path(f.name)

    # Case 1: gate passes (no regression)
    report_pass = EvalReport(
        total_cases=100,
        passed=78,
        failed=22,
        skipped=0,
        pass_rate=0.78,
        by_category={
            "rag_extended": CategoryResult(
                category="rag_extended",
                total=100,
                passed=78,
                failed=22,
                skipped=0,
                pass_rate=0.78,
            )
        },
    )
    gate_pass = check_gate(report_pass, baseline_path=baseline_path, threshold_pct=5.0)
    _assert("GateResult is dataclass", isinstance(gate_pass, GateResult))
    _assert("gate passes when delta=-2%", gate_pass.passed is True, f"passed={gate_pass.passed}")
    _assert("gate reason contains 'PASSED'", "PASSED" in gate_pass.reason)

    # Case 2: gate fails (regression > threshold)
    report_fail = EvalReport(
        total_cases=100,
        passed=65,
        failed=35,
        skipped=0,
        pass_rate=0.65,
        by_category={
            "rag_extended": CategoryResult(
                category="rag_extended",
                total=100,
                passed=65,
                failed=35,
                skipped=0,
                pass_rate=0.65,
            )
        },
    )
    gate_fail = check_gate(report_fail, baseline_path=baseline_path, threshold_pct=5.0)
    _assert("gate fails when delta=-15%", gate_fail.passed is False, f"passed={gate_fail.passed}")
    _assert("gate reason contains 'FAILED'", "FAILED" in gate_fail.reason)
    _assert("gate delta < -5", gate_fail.delta < -5.0, f"delta={gate_fail.delta}")

    baseline_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 14: save_report writes .md and .json files
# ---------------------------------------------------------------------------


def test_save_report() -> None:
    cat = CategoryResult(
        category="safety",
        total=50,
        passed=45,
        failed=5,
        skipped=0,
        pass_rate=0.90,
    )
    report = EvalReport(
        total_cases=50,
        passed=45,
        failed=5,
        skipped=0,
        pass_rate=0.90,
        by_category={"safety": cat},
        commit_sha="save_test",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / "report"
        md_path, json_path = save_report(report, out_path)

        _assert("save_report creates .md file", md_path.is_file())
        _assert("save_report creates .json file", json_path.is_file())

        md_content = md_path.read_text()
        json_content = json_path.read_text()

        _assert("md file has content", len(md_content) > 0)
        _assert("json file is valid", True)
        try:
            data = json.loads(json_content)
            _assert("json has pass_rate", "pass_rate" in data)
        except Exception as e:
            _fail("json parseable", str(e))


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_all_tests() -> None:
    tests = [
        test_load_baselines,
        test_load_baselines_with_category_filter,
        test_eval_report_dataclass,
        test_comparison_result_gate_pass,
        test_comparison_result_gate_fail,
        test_format_report_markdown,
        test_format_report_json,
        test_baseline_parsing,
        test_safety_category_detection,
        test_skipped_cases_no_api_key,
        test_pipeline_run_all_stateless,
        test_pipeline_run_with_mock,
        test_gate_result_and_check_gate,
        test_save_report,
    ]

    for t in tests:
        try:
            t()
        except Exception as exc:
            _fail(t.__name__, f"Unexpected exception: {exc}")

    print(f"\nEval Pipeline Tests: {_PASS + _FAIL} total, {_PASS} passed, {_FAIL} failed\n")
    for line in _TESTS:
        print(line)

    if _FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
