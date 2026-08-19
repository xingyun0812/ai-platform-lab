"""tests/test_memory_routes_classifier.py — Classification Override API 单元测试 (Issue X5e).

Run:
    python3 tests/test_memory_routes_classifier.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from apps.gateway.memory_routes import router as memory_router  # noqa: E402
from packages.memory.metrics import reset_metrics_for_tests  # noqa: E402
from packages.memory.store import (  # noqa: E402
    InMemoryMemoryStore,
    MemoryGovernanceConfig,
    MemoryRecord,
    reset_memory_store_for_tests,
)
from packages.platform import configure as _configure_platform  # noqa: E402
from packages.platform.testing import InMemoryPlatformPort  # noqa: E402

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
    _configure_platform(InMemoryPlatformPort())


# ------------------------------------------------------------------------- #
# Helpers
# ------------------------------------------------------------------------- #


def _mock_tenants():
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
    store = InMemoryMemoryStore(governance_config=MemoryGovernanceConfig(min_content_length=1))
    return patch("apps.gateway.memory_routes.get_memory_store", return_value=store)


async def _seed_memory(store: InMemoryMemoryStore, memory_id: str = "mem-test-001") -> str:
    r = MemoryRecord(
        memory_id=memory_id,
        tenant_id="admin-tenant",
        scope="user",
        scope_id="u1",
        content="User prefers dark mode in the dashboard",
        metadata={"class": "preference", "source": "extraction"},
        expires_at=None,
    )
    await store.add(r)
    return r.memory_id


# ------------------------------------------------------------------------- #
# Tests
# ------------------------------------------------------------------------- #


def test_classify_preference_returns_200():
    """PATCH /internal/memory/{id}/classify with preference returns 200."""
    _setup()
    store = InMemoryMemoryStore(governance_config=MemoryGovernanceConfig(min_content_length=1))

    import asyncio

    mid = asyncio.run(_seed_memory(store, "mem-pref-01"))

    with _mock_tenants(), patch("apps.gateway.memory_routes.get_memory_store", return_value=store):
        resp = CLIENT.patch(
            f"/internal/memory/{mid}/classify",
            headers=ADMIN_HEADERS,
            json={"class_label": "preference"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["class"] == "preference"
    assert data["scope"] == "user"
    assert data["expires_at"] is None
    assert data["updated"] is True
    # Verify in-memory update persisted
    r = asyncio.run(store.get(mid))
    assert r is not None
    assert r.metadata.get("class") == "preference"
    assert r.metadata.get("class_source") == "manual"
    assert r.metadata.get("feedback_bonus") == 0.2
    print("PASS test_classify_preference_returns_200")


def test_classify_ephemeral_sets_scope_session():
    """PATCH with ephemeral sets scope=session and expires_at within 24h."""
    _setup()
    store = InMemoryMemoryStore(governance_config=MemoryGovernanceConfig(min_content_length=1))

    import asyncio

    mid = asyncio.run(_seed_memory(store, "mem-eph-01"))

    with _mock_tenants(), patch("apps.gateway.memory_routes.get_memory_store", return_value=store):
        resp = CLIENT.patch(
            f"/internal/memory/{mid}/classify",
            headers=ADMIN_HEADERS,
            json={"class_label": "ephemeral"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["class"] == "ephemeral"
    assert data["scope"] == "session"
    assert data["expires_at"] is not None
    # Should be ~86400 seconds from now
    assert abs(data["expires_at"] - time.time() - 86400) < 5
    print("PASS test_classify_ephemeral_sets_scope_session")


def test_classify_factual_strips_feedback_bonus():
    """PATCH with factual removes feedback_bonus from metadata."""
    _setup()
    store = InMemoryMemoryStore(governance_config=MemoryGovernanceConfig(min_content_length=1))

    import asyncio

    mid = asyncio.run(_seed_memory(store, "mem-fact-01"))
    # Pre-set a feedback_bonus
    r = asyncio.run(store.get(mid))
    assert r is not None
    r.metadata["feedback_bonus"] = 0.5

    with _mock_tenants(), patch("apps.gateway.memory_routes.get_memory_store", return_value=store):
        resp = CLIENT.patch(
            f"/internal/memory/{mid}/classify",
            headers=ADMIN_HEADERS,
            json={"class_label": "factual"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["class"] == "factual"
    assert data["scope"] == "user"
    assert data["expires_at"] is None
    # feedback_bonus should have been removed
    r2 = asyncio.run(store.get(mid))
    assert r2 is not None
    assert "feedback_bonus" not in r2.metadata
    print("PASS test_classify_factual_strips_feedback_bonus")


def test_classify_invalid_class_returns_422():
    """PATCH with invalid class_label returns 422 validation error."""
    _setup()
    with _mock_tenants(), _mock_store():
        resp = CLIENT.patch(
            "/internal/memory/mem-none/classify",
            headers=ADMIN_HEADERS,
            json={"class_label": "invalid_class"},
        )
    assert resp.status_code == 422
    print("PASS test_classify_invalid_class_returns_422")


def test_classify_nonexistent_memory_returns_404():
    """PATCH non-existent memory returns 404."""
    _setup()
    with _mock_tenants(), _mock_store():
        resp = CLIENT.patch(
            "/internal/memory/mem-nonexistent/classify",
            headers=ADMIN_HEADERS,
            json={"class_label": "preference"},
        )
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"]["code"] == "NOT_FOUND"
    print("PASS test_classify_nonexistent_memory_returns_404")


def test_classify_non_admin_returns_403():
    """PATCH without admin role returns 403."""
    _setup()
    store = InMemoryMemoryStore(governance_config=MemoryGovernanceConfig(min_content_length=1))

    import asyncio

    mid = asyncio.run(_seed_memory(store, "mem-auth-01"))

    with _mock_tenants(), patch("apps.gateway.memory_routes.get_memory_store", return_value=store):
        resp = CLIENT.patch(
            f"/internal/memory/{mid}/classify",
            headers=USER_HEADERS,
            json={"class_label": "preference"},
        )
    assert resp.status_code == 403
    print("PASS test_classify_non_admin_returns_403")


def test_update_metadata_inmemory():
    """InMemoryMemoryStore.update_metadata updates metadata correctly."""
    _setup()
    store = InMemoryMemoryStore(governance_config=MemoryGovernanceConfig(min_content_length=1))

    import asyncio

    mid = asyncio.run(_seed_memory(store, "mem-upd-01"))

    result = asyncio.run(store.update_metadata(mid, {"new_key": "new_value"}))
    assert result is True

    r = asyncio.run(store.get(mid))
    assert r is not None
    assert r.metadata.get("new_key") == "new_value"
    print("PASS test_update_metadata_inmemory")


def test_update_metadata_nonexistent():
    """update_metadata returns False for non-existent memory."""
    _setup()
    store = InMemoryMemoryStore(governance_config=MemoryGovernanceConfig(min_content_length=1))

    import asyncio

    result = asyncio.run(store.update_metadata("mem-noexist", {"k": "v"}))
    assert result is False
    print("PASS test_update_metadata_nonexistent")


# ------------------------------------------------------------------------- #
# Main
# ------------------------------------------------------------------------- #


def main() -> int:
    tests = [
        test_classify_preference_returns_200,
        test_classify_ephemeral_sets_scope_session,
        test_classify_factual_strips_feedback_bonus,
        test_classify_invalid_class_returns_422,
        test_classify_nonexistent_memory_returns_404,
        test_classify_non_admin_returns_403,
        test_update_metadata_inmemory,
        test_update_metadata_nonexistent,
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
