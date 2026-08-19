"""tests/test_memory_classifier_llm.py — X5b LLM Classifier 单元测试.

Run:
    python3 tests/test_memory_classifier_llm.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import asyncio

from packages.memory.classifier import run_classifier
from packages.memory.classifier.llm import (
    _parse_llm_response,
    llm_classify,
)
from packages.memory.config import MemoryGovernanceConfig

# ------------------------------------------------------------------------- #
# Helpers
# ------------------------------------------------------------------------- #


def _make_cfg() -> MemoryGovernanceConfig:
    return MemoryGovernanceConfig(
        classifier_enabled=True,
        classifier_rule_enabled=True,
        classifier_llm_fallback_class="ephemeral",
        classifier_timeout_ms=200,
    )


# ------------------------------------------------------------------------- #
# Tests: llm_classify
# ------------------------------------------------------------------------- #


def test_llm_returns_preference():
    """LLM returns preference -> ClassResult with class_label='preference'."""

    async def run():
        async def mock_llm(prompt: str) -> str:
            return (
                '{"class": "preference", "confidence": 0.95, "reason": "user preference detected"}'
            )

        cfg = _make_cfg()
        result = await llm_classify("我喜欢简洁的回答", cfg, llm_call=mock_llm)
        assert result.class_label == "preference", f"expected preference, got {result.class_label}"
        assert result.confidence == 0.95, f"expected 0.95, got {result.confidence}"
        assert result.source == "llm", f"expected llm, got {result.source}"

    asyncio.run(run())
    print("PASS test_llm_returns_preference")


def test_llm_returns_factual():
    """LLM returns factual -> ClassResult with class_label='factual'."""

    async def run():
        async def mock_llm(prompt: str) -> str:
            return '{"class": "factual", "confidence": 0.88, "reason": "factual info"}'

        cfg = _make_cfg()
        result = await llm_classify("服务运行在 Python 3.11", cfg, llm_call=mock_llm)
        assert result.class_label == "factual"
        assert result.confidence == 0.88
        assert result.source == "llm"

    asyncio.run(run())
    print("PASS test_llm_returns_factual")


def test_llm_returns_ephemeral():
    """LLM returns ephemeral -> ClassResult with class_label='ephemeral'."""

    async def run():
        async def mock_llm(prompt: str) -> str:
            return '{"class": "ephemeral", "confidence": 0.75, "reason": "session state"}'

        cfg = _make_cfg()
        result = await llm_classify("我们在调试 Issue #221", cfg, llm_call=mock_llm)
        assert result.class_label == "ephemeral"
        assert result.confidence == 0.75

    asyncio.run(run())
    print("PASS test_llm_returns_ephemeral")


def test_llm_returns_noise():
    """LLM returns noise -> ClassResult with class_label='noise'."""

    async def run():
        async def mock_llm(prompt: str) -> str:
            return '{"class": "noise", "confidence": 0.99, "reason": "chitchat"}'

        cfg = _make_cfg()
        result = await llm_classify("嗯嗯", cfg, llm_call=mock_llm)
        assert result.class_label == "noise"
        assert result.confidence == 0.99

    asyncio.run(run())
    print("PASS test_llm_returns_noise")


def test_llm_timeout_defaults_to_ephemeral():
    """LLM timeout (empty response) -> default ephemeral."""

    async def run():
        async def mock_llm(prompt: str) -> str:
            return ""

        cfg = _make_cfg()
        result = await llm_classify("some content", cfg, llm_call=mock_llm)
        assert result.class_label == "ephemeral"
        assert result.confidence == 0.5
        assert result.source == "default"

    asyncio.run(run())
    print("PASS test_llm_timeout_defaults_to_ephemeral")


def test_llm_invalid_json_defaults_to_ephemeral():
    """LLM returns invalid JSON -> default ephemeral."""

    async def run():
        async def mock_llm(prompt: str) -> str:
            return "not json at all"

        cfg = _make_cfg()
        result = await llm_classify("some content", cfg, llm_call=mock_llm)
        assert result.class_label == "ephemeral"
        assert result.confidence == 0.5
        assert result.source == "default"

    asyncio.run(run())
    print("PASS test_llm_invalid_json_defaults_to_ephemeral")


def test_llm_markdown_wrapped_json_parsed():
    """LLM returns markdown-wrapped JSON -> still parses correctly."""

    async def run():
        async def mock_llm(prompt: str) -> str:
            return """```json
{"class": "factual", "confidence": 0.9, "reason": "wrapped in markdown"}
```"""

        cfg = _make_cfg()
        result = await llm_classify("something", cfg, llm_call=mock_llm)
        assert result.class_label == "factual"
        assert result.confidence == 0.9
        assert result.source == "llm"

    asyncio.run(run())
    print("PASS test_llm_markdown_wrapped_json_parsed")


def test_llm_invalid_class_label_falls_back():
    """LLM returns invalid class label -> falls back to default."""

    async def run():
        async def mock_llm(prompt: str) -> str:
            return '{"class": "unknown_type", "confidence": 0.9, "reason": "test"}'

        cfg = _make_cfg()
        result = await llm_classify("some content", cfg, llm_call=mock_llm)
        assert result.class_label == "ephemeral"
        assert result.confidence == 0.5

    asyncio.run(run())
    print("PASS test_llm_invalid_class_label_falls_back")


def test_run_classifier_rule_wins_before_llm():
    """run_classifier with rule match -> returns rule result, not LLM."""

    async def run():
        llm_called = False

        async def mock_llm(prompt: str) -> str:
            nonlocal llm_called
            llm_called = True
            return '{"class": "factual", "confidence": 0.9, "reason": ""}'

        cfg = _make_cfg()
        result = await run_classifier("好的", cfg, llm_call=mock_llm)
        assert result.class_label == "noise", f"expected noise, got {result.class_label}"
        assert result.source == "rule"
        assert not llm_called, "LLM was called but rule should have matched first"

    asyncio.run(run())
    print("PASS test_run_classifier_rule_wins_before_llm")


def test_run_classifier_rule_returns_none_then_llm():
    """run_classifier: rule returns None -> LLM is called."""

    async def run():
        async def mock_llm(prompt: str) -> str:
            return '{"class": "ephemeral", "confidence": 0.85, "reason": "detected by LLM"}'

        cfg = _make_cfg()
        result = await run_classifier("这个系统使用了不确定类别的描述内容", cfg, llm_call=mock_llm)
        assert result.class_label == "ephemeral"
        assert result.source == "llm"

    asyncio.run(run())
    print("PASS test_run_classifier_rule_returns_none_then_llm")


def test_parse_llm_response_markdown_backtick_only():
    """_parse_llm_response handles backtick-only (no json prefix)."""
    content = """```
{"class": "preference", "confidence": 0.8, "reason": "test"}
```"""
    cfg = _make_cfg()
    result = _parse_llm_response(content, cfg)
    assert result.class_label == "preference"
    assert result.confidence == 0.8
    print("PASS test_parse_llm_response_markdown_backtick_only")


def test_run_classifier_disabled_rule_still_calls_llm():
    """run_classifier with rule disabled -> LLM is called."""

    async def run():
        async def mock_llm(prompt: str) -> str:
            return '{"class": "preference", "confidence": 0.9, "reason": "LLM path"}'

        cfg = _make_cfg()
        cfg.classifier_rule_enabled = False
        result = await run_classifier("好的", cfg, llm_call=mock_llm)
        assert result.class_label == "preference"
        assert result.source == "llm"

    asyncio.run(run())
    print("PASS test_run_classifier_disabled_rule_still_calls_llm")


def test_llm_call_provided_used_instead_of_real():
    """Mock llm_call provided -> used instead of real LLM."""

    async def run():
        async def custom_llm(prompt: str) -> str:
            assert "记忆内容" in prompt, f"expected 记忆内容 in prompt, got: {prompt}"
            return '{"class": "factual", "confidence": 0.95, "reason": "custom mock"}'

        cfg = _make_cfg()
        result = await llm_classify("something", cfg, llm_call=custom_llm)
        assert result.class_label == "factual"
        assert result.confidence == 0.95
        assert result.reason == "custom mock"

    asyncio.run(run())
    print("PASS test_llm_call_provided_used_instead_of_real")


def test_run_classifier_disabled_returns_factual():
    """run_classifier with classifier disabled -> returns factual with source=default."""

    async def run():
        cfg = _make_cfg()
        cfg.classifier_enabled = False
        result = await run_classifier("anything", cfg)
        assert result.class_label == "factual"
        assert result.source == "default"
        assert result.reason == "classifier disabled"

    asyncio.run(run())
    print("PASS test_run_classifier_disabled_returns_factual")


# ------------------------------------------------------------------------- #
# Main
# ------------------------------------------------------------------------- #


def main() -> int:
    tests = [
        test_llm_returns_preference,
        test_llm_returns_factual,
        test_llm_returns_ephemeral,
        test_llm_returns_noise,
        test_llm_timeout_defaults_to_ephemeral,
        test_llm_invalid_json_defaults_to_ephemeral,
        test_llm_markdown_wrapped_json_parsed,
        test_llm_invalid_class_label_falls_back,
        test_run_classifier_rule_wins_before_llm,
        test_run_classifier_rule_returns_none_then_llm,
        test_parse_llm_response_markdown_backtick_only,
        test_run_classifier_disabled_rule_still_calls_llm,
        test_llm_call_provided_used_instead_of_real,
        test_run_classifier_disabled_returns_factual,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
