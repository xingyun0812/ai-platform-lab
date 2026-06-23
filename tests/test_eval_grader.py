#!/usr/bin/env python3
"""tests/test_eval_grader.py — Phase L #56 LLM-as-Judge 单测。"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


grader_mod = _load("eval.grader", REPO_ROOT / "eval" / "grader.py")
CaseGrader = grader_mod.CaseGrader
_parse_judge_json = grader_mod._parse_judge_json


class TestKeywordGrading(unittest.TestCase):
    def setUp(self) -> None:
        self.grader = CaseGrader(api_key="")

    def test_keyword_match(self) -> None:
        case = {"expected_keywords": ["RAG", "pipeline"]}
        result = {"answer": "This RAG pipeline indexes documents."}
        passed, reason, mode = self.grader.grade_rag(case, result)
        self.assertTrue(passed)
        self.assertEqual(mode, "keyword")

    def test_keyword_miss(self) -> None:
        case = {"expected_keywords": ["quantum"]}
        result = {"answer": "hello world"}
        passed, _, mode = self.grader.grade_rag(case, result)
        self.assertFalse(passed)
        self.assertEqual(mode, "keyword")

    def test_expect_no_answer(self) -> None:
        case = {"expect_no_answer": True}
        result = {"answer": "I don't know based on the context."}
        passed, _, _ = self.grader.grade_rag(case, result)
        self.assertTrue(passed)


class TestJudgeJson(unittest.TestCase):
    def test_parse_raw_json(self) -> None:
        parsed = _parse_judge_json('{"pass": true, "score": 0.9, "reason": "ok"}')
        self.assertTrue(parsed["pass"])

    def test_parse_embedded_json(self) -> None:
        parsed = _parse_judge_json('Here is result: {"pass": false, "score": 0.2, "reason": "bad"}')
        self.assertFalse(parsed["pass"])


class TestLlmJudge(unittest.TestCase):
    @patch.object(CaseGrader, "_call_judge", return_value='{"pass": true, "score": 0.85, "reason": "grounded"}')
    def test_llm_judge_pass(self, _mock: object) -> None:
        grader = CaseGrader(api_key="sk-test")
        case = {"grading": "llm_judge", "query": "what is RAG?", "expected_keywords": ["RAG"]}
        result = {"answer": "RAG retrieves and generates."}
        passed, reason, mode = grader.grade_rag(case, result)
        self.assertTrue(passed)
        self.assertEqual(mode, "llm_judge")
        self.assertIn("0.85", reason)

    @patch.object(CaseGrader, "_call_judge", side_effect=RuntimeError("upstream down"))
    def test_llm_judge_fallback_keyword(self, _mock: object) -> None:
        grader = CaseGrader(api_key="sk-test")
        case = {"grading": "llm_judge", "expected_keywords": ["RAG"]}
        result = {"answer": "RAG is retrieval augmented generation"}
        passed, reason, mode = grader.grade_rag(case, result)
        self.assertTrue(passed)
        self.assertEqual(mode, "keyword_fallback")
        self.assertIn("fallback", reason)

    @patch.object(CaseGrader, "_call_judge", return_value='{"pass": false, "score": 0.1, "reason": "hallucination"}')
    def test_llm_judge_fail(self, _mock: object) -> None:
        grader = CaseGrader(api_key="sk-test")
        case = {"grading": "llm_judge", "query": "x"}
        result = {"answer": "wrong"}
        passed, _, mode = grader.grade_rag(case, result)
        self.assertFalse(passed)
        self.assertEqual(mode, "llm_judge")

    def test_llm_judge_without_key_uses_keyword(self) -> None:
        grader = CaseGrader(api_key="")
        case = {"grading": "llm_judge", "expected_keywords": ["foo"]}
        result = {"answer": "contains foo"}
        passed, _, mode = grader.grade_rag(case, result)
        self.assertTrue(passed)
        self.assertEqual(mode, "keyword")


class TestPipelineGradingStats(unittest.TestCase):
    def test_run_all_grading_stats_empty_without_live(self) -> None:
        pipeline_mod = _load("eval.pipeline", REPO_ROOT / "eval" / "pipeline.py")
        pipeline = pipeline_mod.EvalPipeline(api_key=None)
        report = pipeline.run_all(categories=["safety"], sample_limit=3)
        self.assertIsInstance(report.grading_stats, dict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
