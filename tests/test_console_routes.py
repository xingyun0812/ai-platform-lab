"""Console V2 适配 API 单测。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_main():
    spec = importlib.util.spec_from_file_location(
        "gateway_main_console_test",
        REPO_ROOT / "apps" / "gateway" / "main.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.app


def _headers(tenant: str = "admin", token: str = "sk-tenant-admin-change-me") -> dict[str, str]:
    return {"X-Tenant-Id": tenant, "Authorization": f"Bearer {token}"}


def test_console_static_mount():
    app = _load_main()
    client = TestClient(app)
    static_index = REPO_ROOT / "apps" / "console" / "static" / "index.html"
    if not static_index.is_file():
        return
    resp = client.get("/console/")
    assert resp.status_code == 200
    assert "root" in resp.text


def test_console_auth_token():
    app = _load_main()
    client = TestClient(app)
    ok = client.post(
        "/internal/auth/token",
        json={"tenant_id": "admin", "api_key": "sk-tenant-admin-change-me"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["tenant_id"] == "admin"
    assert body["token"]

    bad = client.post(
        "/internal/auth/token",
        json={"tenant_id": "admin", "api_key": "wrong"},
    )
    assert bad.status_code == 401


def test_console_tenants_and_settings():
    app = _load_main()
    client = TestClient(app)
    h = _headers()
    tenants = client.get("/internal/tenants", headers=h)
    assert tenants.status_code == 200
    data = tenants.json()
    assert isinstance(data, list)
    assert any(t["tenant_id"] == "admin" for t in data)
    admin_row = next(t for t in data if t["tenant_id"] == "admin")
    assert "billing_available" in admin_row
    assert "tokens_used_this_month" in admin_row

    settings = client.get("/internal/settings", headers=h)
    assert settings.status_code == 200
    assert "default_model" in settings.json()


def test_console_tenants_monthly_usage_from_billing():
    from unittest.mock import patch

    from packages.billing.budget import BudgetSnapshot

    app = _load_main()
    client = TestClient(app)
    snap = BudgetSnapshot(
        used_daily=1200,
        used_monthly=45678,
        remaining_daily=None,
        remaining_monthly=None,
        token_budget_daily=-1,
        token_budget_monthly=5000,
    )

    with patch(
        "apps.gateway.console_routes.get_budget_snapshot",
        return_value=snap,
    ):
        resp = client.get("/internal/tenants", headers=_headers())
    assert resp.status_code == 200
    demo_b = next((t for t in resp.json() if t["tenant_id"] == "demo-b"), None)
    if demo_b is not None:
        assert demo_b["billing_available"] is True
        assert demo_b["tokens_used_this_month"] == 45678


def test_console_metrics():
    app = _load_main()
    client = TestClient(app)
    resp = client.get("/internal/metrics", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert "qps" in body
    assert "tokens_by_tenant" in body


def test_console_rag_knowledge_bases():
    app = _load_main()
    client = TestClient(app)
    resp = client.get("/internal/rag/knowledge-bases", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert any(item["kb_id"] == "lab-demo" for item in body)
