"""tests/test_experience_rerank.py — Phase R PRD-3 Rerank 测试。

验证 rerank_experiences() 的 LLM judge 逻辑：
- Mock LLM 返回全部 relevant → 全部保留
- Mock LLM 返回部分 relevant → 仅保留对应的
- Mock LLM 返回空 → fail-open（全部保留）
- LLM 调用失败/超时 → fail-open（全部保留）
- max_relevant 截断
- 缓存命中不产生 LLM 调用
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from packages.agent.experience_store import (  # noqa: E402
    ExperienceRecord,
    _check_rerank_cache,
    _parse_rerank_json,
    _rerank_cache_key,
    clear_rerank_cache,
    rerank_experiences,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_async(coro):
    return asyncio.run(coro)


def _make_plan() -> AgentPlan:
    return AgentPlan(
        goal="test",
        steps=[PlanStep(id="step-1", description="do something", tool_hint="test")],
        reasoning="test",
    )


def _make_exp(
    experience_id: str,
    goal: str = "test goal",
    outcome: str = "success",
    lessons: str = "some lessons",
) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=experience_id,
        tenant_id="test_tenant",
        task_signature="abc123",
        goal=goal,
        plan=_make_plan(),
        tool_calls=[],
        outcome=outcome,
        lessons=lessons,
        created_at=time.time(),
    )


class MockRoute:
    """模拟 route 返回对象。"""

    def __init__(self, status: int, body: dict | None = None):
        self.status = status
        self.body = body


# ---------------------------------------------------------------------------
# _parse_rerank_json 单元测试
# ---------------------------------------------------------------------------


class TestParseRerankJson:
    def test_parse_valid_json(self):
        content = (
            '[{"index": 0, "relevant": true, "reason": "matches"},'
            ' {"index": 1, "relevant": false, "reason": "no"}]'
        )
        result = _parse_rerank_json(content, 2)
        assert result == [0]

    def test_parse_valid_json_all_relevant(self):
        content = '[{"index": 0, "relevant": true}, {"index": 1, "relevant": true}]'
        result = _parse_rerank_json(content, 2)
        assert result == [0, 1]

    def test_parse_valid_json_none_relevant(self):
        content = '[{"index": 0, "relevant": false}, {"index": 1, "relevant": false}]'
        result = _parse_rerank_json(content, 2)
        assert result == []

    def test_parse_markdown_code_block(self):
        content = """```json
[{"index": 0, "relevant": true, "reason": "yes"}]
```"""
        result = _parse_rerank_json(content, 1)
        assert result == [0]

    def test_parse_code_block_no_language(self):
        content = """```
[{"index": 0, "relevant": true}]
```"""
        result = _parse_rerank_json(content, 1)
        assert result == [0]

    def test_parse_invalid_raises(self):
        with pytest.raises(Exception):
            _parse_rerank_json("not json at all", 2)

    def test_parse_index_out_of_range_skipped(self):
        content = '[{"index": 99, "relevant": true}]'
        result = _parse_rerank_json(content, 2)
        assert result == []


# ---------------------------------------------------------------------------
# rerank_experiences 测试
# ---------------------------------------------------------------------------


class TestRerankExperiences:
    def test_empty_experiences(self):
        """空列表返回空。"""
        result = _run_async(rerank_experiences("test", []))
        assert result == []

    @patch("packages.platform.forward_with_model_router")
    def test_mock_returns_all_relevant(self, mock_forward):
        """LLM 返回全部 relevant → 全部保留（受 max_relevant 限制）。"""
        clear_rerank_cache()
        exps = [
            _make_exp("exp-1", goal="build login page"),
            _make_exp("exp-2", goal="implement auth"),
            _make_exp("exp-3", goal="add logging"),
        ]
        mock_forward.return_value = MockRoute(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '[{"index": 0, "relevant": true, "reason": "a"},'
                                '{"index": 1, "relevant": true, "reason": "b"},'
                                '{"index": 2, "relevant": true, "reason": "c"}]'
                            )
                        }
                    }
                ]
            },
        )

        # max_relevant=2 -> only first 2
        result = _run_async(rerank_experiences("test goal", exps, max_relevant=2))
        assert len(result) == 2
        assert result[0].experience_id == "exp-1"
        assert result[1].experience_id == "exp-2"

        # verify LLM was called once
        assert mock_forward.call_count == 1

    @patch("packages.platform.forward_with_model_router")
    def test_mock_returns_partial_relevant(self, mock_forward):
        """LLM 返回部分 relevant → 仅保留对应的。"""
        clear_rerank_cache()
        exps = [
            _make_exp("exp-1", goal="build login page"),
            _make_exp("exp-2", goal="implement auth"),
            _make_exp("exp-3", goal="add logging"),
        ]
        mock_forward.return_value = MockRoute(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '[{"index": 0, "relevant": true, "reason": "a"},'
                                '{"index": 1, "relevant": false, "reason": "b"},'
                                '{"index": 2, "relevant": true, "reason": "c"}]'
                            )
                        }
                    }
                ]
            },
        )

        result = _run_async(rerank_experiences("test goal", exps, max_relevant=5))
        assert len(result) == 2
        ids = {r.experience_id for r in result}
        assert ids == {"exp-1", "exp-3"}

    @patch("packages.platform.forward_with_model_router")
    def test_mock_returns_empty(self, mock_forward):
        """LLM 返回空 JSON 数组 → fail-open（全部保留）。"""
        clear_rerank_cache()
        exps = [
            _make_exp("exp-1", goal="build login page"),
            _make_exp("exp-2", goal="implement auth"),
        ]
        mock_forward.return_value = MockRoute(
            200,
            {"choices": [{"message": {"content": "[]"}}]},
        )

        result = _run_async(rerank_experiences("test goal", exps, max_relevant=5))
        assert len(result) == 2

    @patch("packages.platform.forward_with_model_router")
    def test_llm_call_failure(self, mock_forward):
        """LLM 调用失败 → fail-open（全部保留）。"""
        clear_rerank_cache()
        exps = [
            _make_exp("exp-1", goal="build login page"),
            _make_exp("exp-2", goal="implement auth"),
            _make_exp("exp-3", goal="add logging"),
        ]
        mock_forward.side_effect = RuntimeError("API down")

        result = _run_async(rerank_experiences("test goal", exps, max_relevant=5))
        assert len(result) == 3

    @patch("packages.platform.forward_with_model_router")
    def test_llm_call_non_200(self, mock_forward):
        """LLM 返回非 200 状态码 → fail-open（全部保留）。"""
        clear_rerank_cache()
        exps = [
            _make_exp("exp-1", goal="build login page"),
            _make_exp("exp-2", goal="implement auth"),
        ]
        mock_forward.return_value = MockRoute(500, {})

        result = _run_async(rerank_experiences("test goal", exps, max_relevant=5))
        assert len(result) == 2

    @patch("packages.platform.forward_with_model_router")
    def test_max_relevant_limiting(self, mock_forward):
        """最多返回 max_relevant 条。"""
        clear_rerank_cache()
        exps = [
            _make_exp("exp-1", goal="build login page"),
            _make_exp("exp-2", goal="implement auth"),
            _make_exp("exp-3", goal="add logging"),
            _make_exp("exp-4", goal="deploy app"),
        ]
        mock_forward.return_value = MockRoute(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '[{"index": 0, "relevant": true},'
                                '{"index": 1, "relevant": true},'
                                '{"index": 2, "relevant": true},'
                                '{"index": 3, "relevant": true}]'
                            )
                        }
                    }
                ]
            },
        )

        # max_relevant=1 -> only 1 returned
        result = _run_async(rerank_experiences("test goal", exps, max_relevant=1))
        assert len(result) == 1

    @patch("packages.platform.forward_with_model_router")
    def test_cache_hit_returns_without_llm_call(self, mock_forward):
        """相同 (goal, exp_ids) 在缓存时间内返回缓存结果。"""
        clear_rerank_cache()
        exps = [
            _make_exp("exp-1", goal="build login page"),
            _make_exp("exp-2", goal="implement auth"),
            _make_exp("exp-3", goal="add logging"),
        ]

        # 首次调用 — LLM 返回 indices [0, 2]
        mock_forward.return_value = MockRoute(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '[{"index": 0, "relevant": true},'
                                '{"index": 1, "relevant": false},'
                                '{"index": 2, "relevant": true}]'
                            )
                        }
                    }
                ]
            },
        )

        result1 = _run_async(rerank_experiences("test goal", exps, max_relevant=5))
        assert len(result1) == 2
        assert mock_forward.call_count == 1

        # 第二次调用 — 应命中缓存，不调用 LLM
        result2 = _run_async(rerank_experiences("test goal", exps, max_relevant=5))
        assert len(result2) == 2
        # call_count 没有增加
        assert mock_forward.call_count == 1

        # 结果相同
        assert [r.experience_id for r in result1] == [r.experience_id for r in result2]

    @patch("packages.platform.forward_with_model_router")
    def test_cache_miss_with_different_goal(self, mock_forward):
        """不同 goal → 缓存不同。"""
        clear_rerank_cache()
        exps = [_make_exp("exp-1"), _make_exp("exp-2")]

        mock_forward.return_value = MockRoute(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '[{"index": 0, "relevant": true},{"index": 1, "relevant": true}]'
                            )
                        }
                    }
                ]
            },
        )

        _run_async(rerank_experiences("goal A", exps, max_relevant=5))
        assert mock_forward.call_count == 1

        # 不同 goal 应产生新的 LLM 调用
        _run_async(rerank_experiences("goal B", exps, max_relevant=5))
        assert mock_forward.call_count == 2

    def test_cache_invalidation(self):
        """过期后缓存失效。"""
        clear_rerank_cache()
        exps = [_make_exp("exp-1")]
        key = _rerank_cache_key("test", exps)

        # 手动设置一个已经过期的缓存
        import time

        from packages.agent.experience_store import _rerank_cache

        _rerank_cache[key] = (time.time() - 1, [0])  # 已过期

        hit, indices = _check_rerank_cache("test", exps)
        assert hit is False
        # 过期条目已被清理
        assert key not in _rerank_cache

    def test_counter_increment(self):
        """传了 counter 时 LLM 调用后自增。"""
        clear_rerank_cache()
        exps = [_make_exp("exp-1")]

        counter: list[int] = [0]

        with patch("packages.platform.forward_with_model_router") as mock_forward:
            mock_forward.return_value = MockRoute(
                200,
                {"choices": [{"message": {"content": '[{"index": 0, "relevant": true}]'}}]},
            )

            _run_async(rerank_experiences("test goal", exps, max_relevant=5, counter=counter))

        assert counter[0] == 1

    def test_parse_json_failure_fall_open(self):
        """解析失败的 JSON → fail-open（全部保留）。"""
        clear_rerank_cache()
        exps = [
            _make_exp("exp-1", goal="build login page"),
            _make_exp("exp-2", goal="implement auth"),
        ]

        with patch("packages.platform.forward_with_model_router") as mock_forward:
            mock_forward.return_value = MockRoute(
                200,
                {"choices": [{"message": {"content": "this is not json at all"}}]},
            )

            result = _run_async(rerank_experiences("test goal", exps, max_relevant=5))
        assert len(result) == 2
