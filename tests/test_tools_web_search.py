"""Phase O #91 — web_search 工具单测。"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from packages.agent.registry import ToolRegistry, reset_tool_registry_for_tests
from packages.agent.tools.web_search import (
    build_weather_summary,
    ddg_web_search,
    extract_weather_location,
    handle_web_search,
    http_web_search,
    is_weather_query,
    mock_web_search,
    normalize_search_results,
    parse_ddg_html,
    prepend_weather_result,
    wmo_code_to_zh,
)
from packages.audit.action_levels import ActionClassifier


def _run(coro):
    return asyncio.run(coro)


class MockWebSearchTests(unittest.TestCase):
    def test_returns_structured_results(self) -> None:
        results = mock_web_search("RAG pipeline", top_k=3)
        self.assertEqual(len(results), 3)
        self.assertTrue(all({"title", "snippet", "url"} <= set(r) for r in results))

    def test_top_k_respected(self) -> None:
        self.assertEqual(len(mock_web_search("x", top_k=1)), 1)
        self.assertEqual(len(mock_web_search("x", top_k=5)), 5)

    def test_deterministic_for_same_query(self) -> None:
        a = mock_web_search("hello", top_k=2)
        b = mock_web_search("hello", top_k=2)
        self.assertEqual(a, b)


class NormalizeResultsTests(unittest.TestCase):
    def test_from_results_key(self) -> None:
        raw = {"results": [{"title": "T", "snippet": "S", "url": "https://x.com"}]}
        out = normalize_search_results(raw, top_k=5)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "T")

    def test_from_list(self) -> None:
        raw = [{"name": "N", "description": "D", "link": "https://y.com"}]
        out = normalize_search_results(raw, top_k=5)
        self.assertEqual(out[0]["title"], "N")
        self.assertEqual(out[0]["url"], "https://y.com")

    def test_top_k_limit(self) -> None:
        raw = {"results": [{"title": str(i), "snippet": "s", "url": f"https://{i}"} for i in range(10)]}
        self.assertEqual(len(normalize_search_results(raw, top_k=2)), 2)


class ParseDdgHtmlTests(unittest.TestCase):
    def test_parse_sample_html(self) -> None:
        html = """
        <a rel="nofollow" class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fweather.com%2Fcn">昌平天气</a>
        <a class="result__snippet" href="#">今天昌平晴，15°C</a>
        """
        out = parse_ddg_html(html, top_k=3)
        self.assertEqual(len(out), 1)
        self.assertIn("昌平", out[0]["title"])
        self.assertIn("15", out[0]["snippet"])


class WeatherEnrichTests(unittest.TestCase):
    def test_is_weather_query(self) -> None:
        self.assertTrue(is_weather_query("今天昌平天气"))
        self.assertFalse(is_weather_query("企业 SaaS 趋势"))

    def test_extract_location(self) -> None:
        self.assertEqual(extract_weather_location("昌平 天气 今天"), "昌平")
        self.assertEqual(extract_weather_location("帮我看下今天北京昌平的天气"), "北京昌平")

    def test_wmo_code_to_zh(self) -> None:
        self.assertEqual(wmo_code_to_zh(0), "晴")
        self.assertEqual(wmo_code_to_zh(3), "阴")

    def test_build_summary(self) -> None:
        summary = build_weather_summary(
            {
                "location": "昌平",
                "region": "北京市，中国",
                "temperature_c": 28.5,
                "feels_like_c": 30.0,
                "humidity_percent": 40,
                "wind_speed_kmh": 12.0,
                "condition": "多云",
                "observed_at": "2026-06-26T15:00",
            }
        )
        self.assertIn("28.5°C", summary)
        self.assertIn("昌平", summary)

    def test_prepend_weather_result(self) -> None:
        out = prepend_weather_result(
            [{"title": "站点", "snippet": "desc", "url": "https://x.com"}],
            {"location": "昌平", "summary": "昌平当前 20°C，晴。"},
        )
        self.assertEqual(len(out), 2)
        self.assertIn("实时天气", out[0]["title"])
        self.assertIn("20°C", out[0]["snippet"])


class HandleWebSearchTests(unittest.TestCase):
    @patch("packages.platform.get_settings")
    def test_mock_mode_default(self, mock_settings) -> None:
        s = MagicMock()
        s.web_search_mode = "mock"
        s.web_search_url = ""
        s.web_search_top_k = 3
        s.web_search_max_top_k = 10
        s.web_search_timeout_seconds = 10.0
        s.web_search_weather_enrich = True
        mock_settings.return_value = s

        out = _run(handle_web_search({"query": "AI platform"}))
        data = json.loads(out)
        inner = data.get("data") or data
        self.assertEqual(inner.get("mode"), "mock")
        self.assertEqual(len(inner.get("results", [])), 3)

    @patch("packages.platform.get_settings")
    def test_empty_query_error(self, mock_settings) -> None:
        mock_settings.return_value = MagicMock(web_search_mode="mock", web_search_top_k=3, web_search_max_top_k=10)
        out = _run(handle_web_search({"query": "  "}))
        self.assertIn("error", out)

    @patch("packages.platform.get_settings")
    def test_http_mode(self, mock_settings) -> None:
        s = MagicMock()
        s.web_search_mode = "http"
        s.web_search_url = "http://search.internal/api"
        s.web_search_top_k = 2
        s.web_search_max_top_k = 10
        s.web_search_timeout_seconds = 5.0
        mock_settings.return_value = s

        with patch(
            "packages.agent.tools.web_search.http_web_search",
            new_callable=AsyncMock,
            return_value=[{"title": "H", "snippet": "S", "url": "https://h.com"}],
        ) as mock_http:
            out = _run(handle_web_search({"query": "test", "top_k": 2}))
            mock_http.assert_awaited_once()
        data = json.loads(out)
        inner = data.get("data") or data
        self.assertEqual(inner.get("mode"), "http")
        self.assertEqual(len(inner.get("results", [])), 1)

    @patch("packages.platform.get_settings")
    def test_http_failure_fallback_mock(self, mock_settings) -> None:
        s = MagicMock()
        s.web_search_mode = "http"
        s.web_search_url = "http://bad"
        s.web_search_top_k = 2
        s.web_search_max_top_k = 10
        s.web_search_timeout_seconds = 5.0
        mock_settings.return_value = s

        with patch(
            "packages.agent.tools.web_search.http_web_search",
            new_callable=AsyncMock,
            side_effect=RuntimeError("down"),
        ):
            out = _run(handle_web_search({"query": "fallback"}))
        data = json.loads(out)
        inner = data.get("data") or data
        self.assertEqual(inner.get("mode"), "mock_fallback")
        self.assertGreaterEqual(len(inner.get("results", [])), 1)

    @patch("packages.platform.get_settings")
    def test_ddg_mode(self, mock_settings) -> None:
        s = MagicMock()
        s.web_search_mode = "ddg"
        s.web_search_url = ""
        s.web_search_top_k = 2
        s.web_search_max_top_k = 10
        s.web_search_timeout_seconds = 5.0
        s.web_search_weather_enrich = True
        mock_settings.return_value = s

        weather = {
            "location": "昌平",
            "region": "北京市，中国",
            "temperature_c": 28.0,
            "feels_like_c": 29.0,
            "humidity_percent": 35,
            "wind_speed_kmh": 5.0,
            "condition": "多云",
            "observed_at": "2026-06-26T15:00",
            "summary": "昌平（北京市，中国）当前 28.0°C，多云。",
        }

        with (
            patch(
                "packages.agent.tools.web_search.ddg_web_search",
                new_callable=AsyncMock,
                return_value=[{"title": "Weather", "snippet": "15C", "url": "https://w.example"}],
            ) as mock_ddg,
            patch(
                "packages.agent.tools.web_search.fetch_open_meteo_weather",
                new_callable=AsyncMock,
                return_value=weather,
            ) as mock_wx,
        ):
            out = _run(handle_web_search({"query": "北京昌平天气"}))
            mock_ddg.assert_awaited_once()
            mock_wx.assert_awaited_once()
        data = json.loads(out)
        inner = data.get("data") or data
        self.assertEqual(inner.get("mode"), "ddg")
        self.assertEqual(len(inner.get("results", [])), 2)
        self.assertIn("weather", inner)
        self.assertIn("28.0°C", inner["results"][0]["snippet"])

    @patch("packages.platform.get_settings")
    def test_ddg_failure_returns_error_not_mock(self, mock_settings) -> None:
        s = MagicMock()
        s.web_search_mode = "ddg"
        s.web_search_url = ""
        s.web_search_top_k = 2
        s.web_search_max_top_k = 10
        s.web_search_timeout_seconds = 5.0
        mock_settings.return_value = s

        with patch(
            "packages.agent.tools.web_search.ddg_web_search",
            new_callable=AsyncMock,
            side_effect=RuntimeError("blocked"),
        ):
            out = _run(handle_web_search({"query": "北京昌平天气"}))
        data = json.loads(out)
        self.assertIn("error", out if "error" in out else data)


class HttpWebSearchTests(unittest.TestCase):
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    def test_post_json_body(self, mock_post) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {
            "results": [{"title": "A", "snippet": "B", "url": "https://a.com"}]
        }
        mock_post.return_value = mock_resp

        out = _run(
            http_web_search("q", top_k=3, url="http://api/search", timeout_seconds=5.0)
        )
        self.assertEqual(len(out), 1)
        mock_post.assert_awaited_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["query"], "q")


class RegistryAclTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tool_registry_for_tests()

    def tearDown(self) -> None:
        reset_tool_registry_for_tests()

    def test_web_search_registered(self) -> None:
        reg = ToolRegistry()
        tool = reg.get("web_search")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.name, "web_search")

    def test_demo_a_acl_denies_web_search(self) -> None:
        reg = ToolRegistry()
        allowed = ("get_kb_snippet", "calc")
        self.assertFalse(reg.is_allowed("web_search", allowed))


class AuditClassificationTests(unittest.TestCase):
    def test_web_search_is_network(self) -> None:
        clf = ActionClassifier()
        self.assertEqual(clf.classify("web_search"), "network")


class AgentIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        from apps.gateway.settings import get_settings

        get_settings.cache_clear()

    @patch("packages.agent.runner.forward_with_model_router", new_callable=AsyncMock)
    @patch("packages.agent.runner.get_settings")
    def test_run_agent_executes_web_search_tool(self, mock_settings, mock_route) -> None:
        from apps.gateway.settings import Settings
        from packages.agent.runner import run_agent
        from packages.agent.session import SessionStore

        settings = Settings()
        settings.web_search_mode = "mock"
        settings.context_memory_injection_enabled = False
        settings.context_llm_summary_enabled = False
        mock_settings.return_value = settings

        call_count = {"n": 0}

        async def fake_route(payload, requested_model=None):
            call_count["n"] += 1
            if call_count["n"] == 1:

                class R:
                    status = 200
                    body = {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "call_ws1",
                                            "type": "function",
                                            "function": {
                                                "name": "web_search",
                                                "arguments": '{"query":"Phase O demo"}',
                                            },
                                        }
                                    ],
                                }
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    }
                    model_used = "chat-fast"
                    error = None

                return R()

            class R2:
                status = 200
                body = {
                    "choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {},
                }
                model_used = "chat-fast"
                error = None

            return R2()

        mock_route.side_effect = fake_route

        async def _go():
            return await run_agent(
                tenant_id="admin",
                session_id="ws-int",
                new_messages=[{"role": "user", "content": "search web for Phase O"}],
                allowed_tools=("web_search",),
                allowed_models=("chat-fast",),
                model="chat-fast",
                session_store=SessionStore(),
            )

        result = _run(_go())
        trace = result.get("tool_calls") or []
        self.assertTrue(any(getattr(t, "tool_name", None) == "web_search" for t in trace))


if __name__ == "__main__":
    unittest.main()
