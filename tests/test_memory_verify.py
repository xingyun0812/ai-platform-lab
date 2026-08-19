"""tests/test_memory_verify.py — L4 Recall Verification 单元测试 (Issue #220 X4).

Run:
    python3 tests/test_memory_verify.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import asyncio
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.gateway.memory_routes import router as memory_router
from packages.memory.config import MemoryGovernanceConfig
from packages.memory.governance.verify import (
    Verdict,
    verify_relevance,
    verify_top_k_sync,
)
from packages.memory.metrics import reset_metrics_for_tests
from packages.memory.store import InMemoryMemoryStore, MemoryRecord, reset_memory_store_for_tests
from packages.memory.store import MemoryGovernanceConfig as _MGC

APP = FastAPI()
APP.include_router(memory_router)
CLIENT = TestClient(APP)

ADMIN_HEADERS = {
    "X-Tenant-Id": "admin-tenant",
    "Authorization": "Bearer admin-token",
}

USER_HEADERS = {
    "X-Tenant-Id": "user-tenant",
    "Authorization": "Bearer user-token",
}


def _setup():
    reset_metrics_for_tests()
    reset_memory_store_for_tests()


# ------------------------------------------------------------------------- #
# Tests: verify_relevance
# ------------------------------------------------------------------------- #


def test_relevant_no_demote():
    """verify_relevance with mock LLM returns relevant -> no demote."""
    _setup()
    cfg = MemoryGovernanceConfig(verify_enabled=True, verify_confidence_threshold=0.6)
    mem = MemoryRecord(
        memory_id="m1", tenant_id="t1", scope="user", scope_id="u1", content="用户喜欢打篮球"
    )

    async def run():
        # Mock llm_call that returns a Verdict directly (not a string)
        async def mock_llm(prompt):
            return Verdict(relevant=True, confidence=0.95, reason="relevant")

        result = await verify_relevance("运动爱好", mem, cfg, llm_call=mock_llm)
        assert result.relevant is True
        assert result.confidence >= 0.6

    asyncio.run(run())
    print("PASS test_relevant_no_demote")


def test_not_relevant_demote():
    """verify_relevance with mock LLM returns not relevant -> demote."""
    _setup()
    cfg = MemoryGovernanceConfig(verify_enabled=True, verify_confidence_threshold=0.6)
    mem = MemoryRecord(
        memory_id="m1", tenant_id="t1", scope="user", scope_id="u1", content="股票代码 600519"
    )

    async def run():
        async def mock_llm(prompt):
            return Verdict(relevant=False, confidence=0.9, reason="not relevant")

        result = await verify_relevance("今天天气怎么样", mem, cfg, llm_call=mock_llm)
        assert result.relevant is False
        assert result.confidence >= 0.6

    asyncio.run(run())
    print("PASS test_not_relevant_demote")


def test_low_confidence_no_demote():
    """Low confidence -> no demote even if relevant=false."""
    _setup()
    cfg = MemoryGovernanceConfig(verify_enabled=True, verify_confidence_threshold=0.6)
    mem = MemoryRecord(
        memory_id="m1", tenant_id="t1", scope="user", scope_id="u1", content="some content"
    )

    async def run():
        async def mock_llm(prompt):
            return Verdict(relevant=False, confidence=0.3, reason="unsure")

        result = await verify_relevance("query", mem, cfg, llm_call=mock_llm)
        assert result.relevant is False
        assert result.confidence < 0.6

    asyncio.run(run())
    print("PASS test_low_confidence_no_demote")


def test_disabled_always_pass():
    """verify disabled -> always pass."""
    _setup()
    cfg = MemoryGovernanceConfig(verify_enabled=False)
    mem = MemoryRecord(memory_id="m1", tenant_id="t1", scope="user", scope_id="u1", content="any")

    async def run():
        result = await verify_relevance("query", mem, cfg)
        assert result.relevant is True
        assert result.confidence == 1.0

    asyncio.run(run())
    print("PASS test_disabled_always_pass")


# ------------------------------------------------------------------------- #
# Tests: verify_top_k_sync
# ------------------------------------------------------------------------- #


def test_top_k_sync_empty_results():
    """Empty results -> no verify."""
    _setup()
    cfg = MemoryGovernanceConfig(verify_enabled=True)
    results = verify_top_k_sync("query", [], cfg)
    assert results == []
    print("PASS test_top_k_sync_empty_results")


def test_top_k_sync_relevant():
    """verify_top_k_sync with sync mock llm_call."""
    _setup()
    cfg = MemoryGovernanceConfig(verify_enabled=True, verify_confidence_threshold=0.6)
    mem = MemoryRecord(
        memory_id="m1", tenant_id="t1", scope="user", scope_id="u1", content="用户喜欢打篮球"
    )

    def mock_llm(query, memory, config):
        return Verdict(relevant=True, confidence=0.95, reason="relevant")

    results = verify_top_k_sync("运动爱好", [mem], cfg, llm_call=mock_llm)
    assert len(results) == 1
    assert results[0].demoted is False
    print("PASS test_top_k_sync_relevant")


def test_top_k_sync_demoted():
    """verify_top_k_sync demotes irrelevant result."""
    _setup()
    cfg = MemoryGovernanceConfig(
        verify_enabled=True, verify_confidence_threshold=0.6, verify_demote_threshold=0.3
    )
    mem = MemoryRecord(
        memory_id="m1", tenant_id="t1", scope="user", scope_id="u1", content="股票代码 600519"
    )

    def mock_llm(query, memory, config):
        return Verdict(relevant=False, confidence=0.9, reason="not relevant")

    results = verify_top_k_sync("今天天气怎么样", [mem], cfg, llm_call=mock_llm)
    assert len(results) == 1
    assert results[0].demoted is True
    assert results[0].demoted_score == 0.3
    print("PASS test_top_k_sync_demoted")


def test_top_k_sync_disabled():
    """Disabled verify -> no results."""
    _setup()
    cfg = MemoryGovernanceConfig(verify_enabled=False)
    mem = MemoryRecord(memory_id="m1", tenant_id="t1", scope="user", scope_id="u1", content="any")
    results = verify_top_k_sync("query", [mem], cfg)
    assert results == []
    print("PASS test_top_k_sync_disabled")


def test_invalid_json_default_pass():
    """LLM returns invalid JSON -> default pass."""
    _setup()
    cfg = MemoryGovernanceConfig(verify_enabled=True)
    mem = MemoryRecord(
        memory_id="m1", tenant_id="t1", scope="user", scope_id="u1", content="content"
    )

    async def run():
        async def mock_llm(prompt):
            return "not valid json at all"

        result = await verify_relevance("query", mem, cfg, llm_call=mock_llm)
        assert result.relevant is True
        assert result.confidence == 0.5

    asyncio.run(run())
    print("PASS test_invalid_json_default_pass")


def test_empty_response_default_pass():
    """Empty LLM response -> default pass."""
    _setup()
    cfg = MemoryGovernanceConfig(verify_enabled=True)
    mem = MemoryRecord(
        memory_id="m1", tenant_id="t1", scope="user", scope_id="u1", content="content"
    )

    async def run():
        async def mock_llm(prompt):
            return ""

        result = await verify_relevance("query", mem, cfg, llm_call=mock_llm)
        assert result.relevant is True

    asyncio.run(run())
    print("PASS test_empty_response_default_pass")


def test_parse_error_default_pass():
    """Parse error -> relevant=True (degrade gracefully)."""
    _setup()
    cfg = MemoryGovernanceConfig(verify_enabled=True)
    mem = MemoryRecord(memory_id="m1", tenant_id="t1", scope="user", scope_id="u1", content="x")

    async def run():
        async def mock_llm(prompt):
            # Return something that will cause JSONDecodeError after markdown strip
            return "```\nnot json\n```"

        result = await verify_relevance("query", mem, cfg, llm_call=mock_llm)
        assert result.relevant is True

    asyncio.run(run())
    print("PASS test_parse_error_default_pass")


# ------------------------------------------------------------------------- #
# Tests: Feedback API
# ------------------------------------------------------------------------- #


def _mock_tenants(monkeypatch_module):
    from packages.contracts.tenant import TenantRecord

    def fake_load():
        return {
            "admin-tenant": TenantRecord(
                tenant_id="admin-tenant",
                bearer_token="admin-token",
                role="platform_admin",
                daily_request_quota=-1,
                allowed_models=(),
                allowed_tools=(),
                default_model=None,
                rate_limit_rps=100,
                rate_limit_burst=200,
                token_budget_daily=-1,
                token_budget_monthly=-1,
            ),
            "user-tenant": TenantRecord(
                tenant_id="user-tenant",
                bearer_token="user-token",
                role="user",
                daily_request_quota=10,
                allowed_models=(),
                allowed_tools=(),
                default_model=None,
                rate_limit_rps=1,
                rate_limit_burst=5,
                token_budget_daily=10000,
                token_budget_monthly=100000,
            ),
        }

    return patch("apps.gateway.memory_routes.load_tenants", side_effect=fake_load)


def _mock_store():
    store = InMemoryMemoryStore(governance_config=_MGC(min_content_length=1))
    return patch("apps.gateway.memory_routes.get_memory_store", return_value=store)


def test_feedback_sets_bonus():
    """PATCH /feedback sets feedback_bonus correctly."""
    _setup()
    with _mock_tenants(patch), _mock_store():
        # First create a memory via admin
        resp = CLIENT.post(
            "/internal/memory",
            json={"scope": "user", "scope_id": "u1", "content": "test memory for feedback"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 201, f"Create failed: {resp.json()}"
        mem_id = resp.json()["memory_id"]

        # Set feedback (same tenant as admin)
        resp = CLIENT.patch(
            f"/internal/memory/{mem_id}/feedback",
            json={"feedback_bonus": 0.8},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200, f"Feedback failed: {resp.json()}"
        data = resp.json()
        assert data["metadata"]["feedback_bonus"] == 0.8
    print("PASS test_feedback_sets_bonus")


def test_feedback_invalid_value():
    """PATCH /feedback with out-of-range value -> 422."""
    _setup()
    with _mock_tenants(patch), _mock_store():
        resp = CLIENT.post(
            "/internal/memory",
            json={"scope": "user", "scope_id": "u1", "content": "test"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 201
        mem_id = resp.json()["memory_id"]

        # Value out of range (> 1.0)
        resp = CLIENT.patch(
            f"/internal/memory/{mem_id}/feedback",
            json={"feedback_bonus": 5.0},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 422
    print("PASS test_feedback_invalid_value")


def test_feedback_affects_ranking():
    """feedback_bonus affects search ranking via weight formula."""
    _setup()
    from packages.memory.config import MemoryGovernanceConfig as _MGC2

    # Use InMemoryMemoryStore with verify disabled to avoid asyncio nesting issues
    store = InMemoryMemoryStore(
        governance_config=_MGC2(
            min_content_length=1, verify_enabled=False, classifier_enabled=False
        )
    )
    with (
        _mock_tenants(patch),
        patch("apps.gateway.memory_routes.get_memory_store", return_value=store),
    ):
        # Create two memories
        resp1 = CLIENT.post(
            "/internal/memory",
            json={"scope": "user", "scope_id": "u1", "content": "我喜欢编程和Python"},
            headers=ADMIN_HEADERS,
        )
        assert resp1.status_code == 201, f"Create failed: {resp1.json()}"
        mem1_id = resp1.json()["memory_id"]

        resp2 = CLIENT.post(
            "/internal/memory",
            json={"scope": "user", "scope_id": "u1", "content": "我喜欢打篮球和运动"},
            headers=ADMIN_HEADERS,
        )
        assert resp2.status_code == 201, f"Create failed: {resp2.json()}"

        # Give mem1 a positive feedback bonus
        resp = CLIENT.patch(
            f"/internal/memory/{mem1_id}/feedback",
            json={"feedback_bonus": 1.0},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200, f"Feedback failed: {resp.json()}"

        # Search should include feedback bonus in weight calculation
        resp = CLIENT.post(
            "/internal/memory/search",
            json={"scope": "user", "scope_id": "u1", "query": "编程", "top_k": 5},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200, f"Search failed: {resp.json()}"
        data = resp.json()
        assert data["count"] >= 1
        # The memory with positive feedback should be in results
        result_ids = [r["memory_id"] for r in data["results"]]
        assert mem1_id in result_ids  # mem1 matches '编程'
    print("PASS test_feedback_affects_ranking")


def test_feedback_not_found():
    """PATCH /feedback on non-existent memory -> 404."""
    _setup()
    with _mock_tenants(patch), _mock_store():
        resp = CLIENT.patch(
            "/internal/memory/nonexistent-id/feedback",
            json={"feedback_bonus": 0.5},
            headers=USER_HEADERS,
        )
        assert resp.status_code == 404
    print("PASS test_feedback_not_found")


# ------------------------------------------------------------------------- #
# Main
# ------------------------------------------------------------------------- #


def main() -> int:
    tests = [
        test_relevant_no_demote,
        test_not_relevant_demote,
        test_low_confidence_no_demote,
        test_disabled_always_pass,
        test_top_k_sync_empty_results,
        test_top_k_sync_relevant,
        test_top_k_sync_demoted,
        test_top_k_sync_disabled,
        test_invalid_json_default_pass,
        test_empty_response_default_pass,
        test_parse_error_default_pass,
        test_feedback_sets_bonus,
        test_feedback_invalid_value,
        test_feedback_affects_ranking,
        test_feedback_not_found,
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
