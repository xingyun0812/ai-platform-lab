"""主路径 wiring 集成测 — Issue #182 / 架构 §9。"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from packages.contracts.rag_schemas import RetrievedChunk
from packages.router.model_router import ModelRouteResult
from tests.gateway_client import LifespanTestClient

_ADMIN_HEADERS = {
    "X-Tenant-Id": "admin",
    "Authorization": "Bearer sk-tenant-admin-change-me",
}


class TestMainPathWiring(unittest.TestCase):
    def setUp(self) -> None:
        self._client_cm = LifespanTestClient()
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)

    @patch("apps.gateway.chat_routes.forward_with_model_router", new_callable=AsyncMock)
    def test_chat_completions_main_path(self, mock_route: AsyncMock) -> None:
        mock_route.return_value = ModelRouteResult(
            status=200,
            body={
                "choices": [{"message": {"content": "hello from model"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            },
            error=None,
            model_used="gpt-4o-mini",
            models_tried=("gpt-4o-mini",),
            fallback_used=False,
        )
        resp = self.client.post(
            "/v1/chat/completions",
            headers=_ADMIN_HEADERS,
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("choices", resp.json())
        mock_route.assert_awaited()

    @patch("apps.gateway.rag.query_service.forward_with_model_router", new_callable=AsyncMock)
    @patch("apps.gateway.rag.query_service.retrieve_chunks", new_callable=AsyncMock)
    def test_rag_query_main_path(
        self,
        mock_retrieve: AsyncMock,
        mock_route: AsyncMock,
    ) -> None:
        chunk = RetrievedChunk(
            chunk_id="c1",
            kb_id="demo",
            version=1,
            text="ref text",
            source_uri="doc.txt",
            offset=0,
            score=0.95,
        )
        mock_retrieve.return_value = (1, [chunk], None)
        mock_route.return_value = ModelRouteResult(
            status=200,
            body={
                "choices": [{"message": {"content": "rag answer"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
            error=None,
            model_used="gpt-4o-mini",
            models_tried=("gpt-4o-mini",),
            fallback_used=False,
        )
        resp = self.client.post(
            "/v1/rag/query",
            headers=_ADMIN_HEADERS,
            json={
                "tenant_id": "admin",
                "kb_id": "demo",
                "query": "what is in doc?",
                "top_k": 3,
                "min_score": 0.0,
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body.get("answer"), "rag answer")
        mock_retrieve.assert_awaited()
        mock_route.assert_awaited()


class TestOAuth2MiddlewareWiring(unittest.TestCase):
    def test_oauth2_middleware_mounted_when_enabled(self) -> None:
        from unittest.mock import MagicMock, patch

        from apps.gateway.main import create_app
        from apps.gateway.settings import Settings, get_settings

        get_settings.cache_clear()
        base = Settings()
        settings_on = MagicMock(spec=Settings)
        settings_on.oauth2_enabled = True
        settings_on.oauth2_jwt_fallback = True
        settings_on.auth_jwt_secret = "test-secret"
        settings_on.app_name = base.app_name
        settings_on.app_version = base.app_version
        settings_on.audit_enabled = base.audit_enabled
        settings_on.audit_db_path = base.audit_db_path
        settings_on.audit_postgres_enabled = base.audit_postgres_enabled
        settings_on.otel_enabled = base.otel_enabled

        with patch("apps.gateway.main.get_settings", return_value=Settings()):
            app_off = create_app()
        with patch("apps.gateway.main.get_settings", return_value=settings_on):
            app_on = create_app()
        self.assertEqual(len(app_on.user_middleware), len(app_off.user_middleware) + 1)


if __name__ == "__main__":
    unittest.main()
