"""tests/test_memory_routes_governance.py — Governance REST API 单元测试 (Issue #219 X5).

Run:
    python3 tests/test_memory_routes_governance.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from apps.gateway.memory_routes import router as memory_router  # noqa: E402
from packages.memory.metrics import reset_metrics_for_tests  # noqa: E402
from packages.memory.store import reset_memory_store_for_tests  # noqa: E402

APP = FastAPI()
APP.include_router(memory_router)
CLIENT = TestClient(APP)

# Standard admin credentials
ADMIN_HEADERS = {
    "X-Tenant-Id": "admin-tenant",
    "Authorization": "Bearer admin-token",
}

# Non-admin credentials
USER_HEADERS = {
    "X-Tenant-Id": "user-tenant",
    "Authorization": "Bearer user-token",
}


def _setup():
    reset_metrics_for_tests()
    reset_memory_store_for_tests()


# ------------------------------------------------------------------------- #
# Helpers
# ------------------------------------------------------------------------- #


def _mock_tenants(monkeypatch_module):
    """Mock tenant loading for tests."""
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
    """Mock memory store with InMemoryMemoryStore."""
    from packages.memory.store import InMemoryMemoryStore, MemoryGovernanceConfig

    store = InMemoryMemoryStore(governance_config=MemoryGovernanceConfig(min_content_length=1))
    return patch("apps.gateway.memory_routes.get_memory_store", return_value=store)


def _mock_archive():
    """Mock archive store with InMemoryArchiveStore."""
    from packages.memory.archive import InMemoryArchiveStore

    archive = InMemoryArchiveStore()
    return patch("apps.gateway.memory_routes.get_archive_store", return_value=archive)


def _mock_no_store():
    return patch("apps.gateway.memory_routes.get_memory_store", return_value=None)


def _mock_no_archive():
    return patch("apps.gateway.memory_routes.get_archive_store", return_value=None)


# ------------------------------------------------------------------------- #
# Tests
# ------------------------------------------------------------------------- #


def test_governance_run_returns_200():
    """POST /internal/memory/governance/run returns 200 with report."""
    _setup()
    with _mock_tenants(patch), _mock_store(), _mock_archive():
        resp = CLIENT.post("/internal/memory/governance/run", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "report" in data
    assert "duration_seconds" in data
    assert "expired_deleted" in data["report"]
    print("PASS test_governance_run_returns_200")


def test_governance_stats_returns_200():
    """GET /internal/memory/governance/stats returns 200 with counts."""
    _setup()
    with _mock_tenants(patch), _mock_store():
        resp = CLIENT.get("/internal/memory/governance/stats", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "store_available" in data
    assert "purge_counts" in data
    assert "library_totals" in data
    print("PASS test_governance_stats_returns_200")


def test_archive_list_returns_200():
    """GET /internal/memory/archive/list returns 200."""
    _setup()
    with _mock_tenants(patch), _mock_store(), _mock_archive():
        resp = CLIENT.get(
            "/internal/memory/archive/list?scope=user&scope_id=u1",
            headers=ADMIN_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "archived_records" in data
    assert "count" in data
    print("PASS test_archive_list_returns_200")


def test_governance_run_non_admin_returns_403():
    """Non-admin gets 403 for governance endpoints."""
    _setup()
    with _mock_tenants(patch), _mock_store():
        resp = CLIENT.post("/internal/memory/governance/run", headers=USER_HEADERS)
    assert resp.status_code == 403
    print("PASS test_governance_run_non_admin_returns_403")


def test_governance_stats_non_admin_returns_403():
    """Non-admin gets 403 for governance stats."""
    _setup()
    with _mock_tenants(patch), _mock_store():
        resp = CLIENT.get("/internal/memory/governance/stats", headers=USER_HEADERS)
    assert resp.status_code == 403
    print("PASS test_governance_stats_non_admin_returns_403")


def test_archive_list_non_admin_returns_403():
    """Non-admin gets 403 for archive list."""
    _setup()
    with _mock_tenants(patch), _mock_archive():
        resp = CLIENT.get(
            "/internal/memory/archive/list?scope=user&scope_id=u1",
            headers=USER_HEADERS,
        )
    assert resp.status_code == 403
    print("PASS test_archive_list_non_admin_returns_403")


def test_governance_run_no_store_returns_503():
    """Missing store returns 503."""
    _setup()
    with _mock_tenants(patch), _mock_no_store():
        resp = CLIENT.post("/internal/memory/governance/run", headers=ADMIN_HEADERS)
    assert resp.status_code == 503
    print("PASS test_governance_run_no_store_returns_503")


def test_archive_list_no_archive_store_returns_503():
    """Missing archive store returns 503."""
    _setup()
    with _mock_tenants(patch), _mock_store(), _mock_no_archive():
        resp = CLIENT.get(
            "/internal/memory/archive/list?scope=user&scope_id=u1",
            headers=ADMIN_HEADERS,
        )
    assert resp.status_code == 503
    print("PASS test_archive_list_no_archive_store_returns_503")


def test_archive_list_with_data():
    """Archive list returns archived records when data exists."""
    _setup()
    from packages.memory.archive import InMemoryArchiveStore
    from packages.memory.store import InMemoryMemoryStore, MemoryGovernanceConfig, MemoryRecord

    store = InMemoryMemoryStore(governance_config=MemoryGovernanceConfig(min_content_length=1))
    archive = InMemoryArchiveStore()

    async def seed():
        r = MemoryRecord(
            memory_id="m1",
            tenant_id="admin-tenant",
            scope="user",
            scope_id="u1",
            content="archived content",
        )
        await store.add(r)
        await archive.archive(r, purge_reason="test")

    import asyncio

    asyncio.run(seed())

    with (
        _mock_tenants(patch),
        patch("apps.gateway.memory_routes.get_memory_store", return_value=store),
        patch("apps.gateway.memory_routes.get_archive_store", return_value=archive),
    ):
        resp = CLIENT.get(
            "/internal/memory/archive/list?scope=user&scope_id=u1",
            headers=ADMIN_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["archived_records"][0]["purge_reason"] == "test"
    print("PASS test_archive_list_with_data")


# ------------------------------------------------------------------------- #
# Main
# ------------------------------------------------------------------------- #


def main() -> int:
    tests = [
        test_governance_run_returns_200,
        test_governance_stats_returns_200,
        test_archive_list_returns_200,
        test_governance_run_non_admin_returns_403,
        test_governance_stats_non_admin_returns_403,
        test_archive_list_non_admin_returns_403,
        test_governance_run_no_store_returns_503,
        test_archive_list_no_archive_store_returns_503,
        test_archive_list_with_data,
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
