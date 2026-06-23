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

    settings = client.get("/internal/settings", headers=h)
    assert settings.status_code == 200
    assert "default_model" in settings.json()


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
