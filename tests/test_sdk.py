#!/usr/bin/env python3
"""Python SDK 单元测试 — Phase J #45

运行：
    python3 tests/test_sdk.py

依赖：httpx (SDK dep), respx>=0.20 (HTTP mock)
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Add sdk/python to import path so we can import the SDK without installing it
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
SDK_PATH = REPO_ROOT / "sdk" / "python"
sys.path.insert(0, str(SDK_PATH))

import httpx  # noqa: E402 — must be after path injection

from ai_platform_lab import AsyncClient, Client, __version__  # noqa: E402
from ai_platform_lab.exceptions import (  # noqa: E402
    AIPlatformError,
    APIError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS_COUNT = 0
FAIL_COUNT = 0


def _pass(name: str) -> None:
    global PASS_COUNT
    PASS_COUNT += 1
    print(f"PASS  {name}")


def _fail(name: str, err: Exception) -> None:
    global FAIL_COUNT
    FAIL_COUNT += 1
    print(f"FAIL  {name}: {err}")


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_response(status_code: int, body: object) -> httpx.Response:
    """Create a fake httpx.Response with given status and JSON body."""
    content = json.dumps(body).encode()
    return httpx.Response(status_code=status_code, content=content, headers={"content-type": "application/json"})


# ---------------------------------------------------------------------------
# Test 1: __version__ export
# ---------------------------------------------------------------------------

def test_version_export():
    try:
        assert isinstance(__version__, str)
        assert __version__ == "0.1.0"
        _pass("test_version_export")
    except Exception as e:
        _fail("test_version_export", e)


# ---------------------------------------------------------------------------
# Test 2: Client init properties
# ---------------------------------------------------------------------------

def test_client_init():
    try:
        c = Client("http://localhost:8000", api_key="my-key", tenant_id="t1", timeout=10.0)
        assert c._base_url == "http://localhost:8000"
        assert c._api_key == "my-key"
        assert c._tenant_id == "t1"
        c.close()
        _pass("test_client_init")
    except Exception as e:
        _fail("test_client_init", e)


# ---------------------------------------------------------------------------
# Test 3: Client context manager
# ---------------------------------------------------------------------------

def test_client_context_manager():
    try:
        with Client("http://localhost:8000", api_key="k") as c:
            assert c._base_url == "http://localhost:8000"
        # After __exit__ the http client is closed; no exception expected
        _pass("test_client_context_manager")
    except Exception as e:
        _fail("test_client_context_manager", e)


# ---------------------------------------------------------------------------
# Test 4: Header injection — Authorization + X-Tenant-Id
# ---------------------------------------------------------------------------

def test_header_injection():
    try:
        c = Client("http://localhost:8000", api_key="secret-key", tenant_id="tenant-abc")
        resource = c.chat  # ChatResource
        headers = resource._headers()
        assert headers["Authorization"] == "Bearer secret-key"
        assert headers["X-Tenant-Id"] == "tenant-abc"
        c.close()
        _pass("test_header_injection")
    except Exception as e:
        _fail("test_header_injection", e)


# ---------------------------------------------------------------------------
# Test 5: No api_key → no Authorization header
# ---------------------------------------------------------------------------

def test_no_api_key_no_auth_header():
    try:
        c = Client("http://localhost:8000")
        headers = c.memory._headers()
        assert "Authorization" not in headers
        assert "X-Tenant-Id" not in headers
        c.close()
        _pass("test_no_api_key_no_auth_header")
    except Exception as e:
        _fail("test_no_api_key_no_auth_header", e)


# ---------------------------------------------------------------------------
# Test 6: Chat completions sync
# ---------------------------------------------------------------------------

def test_chat_completions_sync():
    try:
        fake_resp = _make_response(200, {"id": "chatcmpl-1", "choices": [{"message": {"content": "Hi"}}]})
        with patch.object(httpx.Client, "request", return_value=fake_resp) as mock_req:
            with Client("http://localhost:8000", api_key="k") as c:
                result = c.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": "Hello"}],
                )
            assert result["id"] == "chatcmpl-1"
            call_kwargs = mock_req.call_args
            assert call_kwargs[0][0] == "POST"
            assert "/v1/chat/completions" in call_kwargs[0][1]
        _pass("test_chat_completions_sync")
    except Exception as e:
        _fail("test_chat_completions_sync", e)


# ---------------------------------------------------------------------------
# Test 7: Chat completions async
# ---------------------------------------------------------------------------

async def _async_chat():
    fake_resp = _make_response(200, {"id": "async-1", "choices": []})
    with patch.object(httpx.AsyncClient, "request", return_value=fake_resp):
        async with AsyncClient("http://localhost:8000", api_key="k") as c:
            return await c.chat.completions.create("gpt-4o", [{"role": "user", "content": "Hi"}])


def test_chat_completions_async():
    try:
        result = _run_async(_async_chat())
        assert result["id"] == "async-1"
        _pass("test_chat_completions_async")
    except Exception as e:
        _fail("test_chat_completions_async", e)


# ---------------------------------------------------------------------------
# Test 8: RAG query
# ---------------------------------------------------------------------------

def test_rag_query():
    try:
        fake_resp = _make_response(200, {"results": [{"text": "chunk", "score": 0.9}]})
        with patch.object(httpx.Client, "request", return_value=fake_resp):
            with Client("http://localhost:8000", api_key="k") as c:
                result = c.rag.query("who is alice?", kb_id="kb1", top_k=3)
        assert "results" in result
        assert result["results"][0]["score"] == 0.9
        _pass("test_rag_query")
    except Exception as e:
        _fail("test_rag_query", e)


# ---------------------------------------------------------------------------
# Test 9: RAG list_kbs
# ---------------------------------------------------------------------------

def test_rag_list_kbs():
    try:
        fake_resp = _make_response(200, [{"kb_id": "kb1"}, {"kb_id": "kb2"}])
        with patch.object(httpx.Client, "request", return_value=fake_resp):
            with Client("http://localhost:8000") as c:
                result = c.rag.list_kbs()
        assert len(result) == 2
        assert result[0]["kb_id"] == "kb1"
        _pass("test_rag_list_kbs")
    except Exception as e:
        _fail("test_rag_list_kbs", e)


# ---------------------------------------------------------------------------
# Test 10: Agent run + list_sessions + get_session
# ---------------------------------------------------------------------------

def test_agent_run_and_sessions():
    try:
        run_resp = _make_response(200, {"reply": "done", "session_id": "s1"})
        list_resp = _make_response(200, [{"session_id": "s1"}])
        get_resp = _make_response(200, {"session_id": "s1", "messages": []})

        responses = iter([run_resp, list_resp, get_resp])
        with patch.object(httpx.Client, "request", side_effect=lambda *a, **kw: next(responses)):
            with Client("http://localhost:8000", api_key="k") as c:
                run = c.agent.run("s1", "Hello", tools=["search"])
                sessions = c.agent.list_sessions()
                session = c.agent.get_session("s1")

        assert run["session_id"] == "s1"
        assert sessions[0]["session_id"] == "s1"
        assert session["session_id"] == "s1"
        _pass("test_agent_run_and_sessions")
    except Exception as e:
        _fail("test_agent_run_and_sessions", e)


# ---------------------------------------------------------------------------
# Test 11: Embedding create + list_models
# ---------------------------------------------------------------------------

def test_embedding_create_and_list():
    try:
        create_resp = _make_response(200, {"data": [{"embedding": [0.1, 0.2]}], "model": "text-embedding-3"})
        list_resp = _make_response(200, [{"model": "text-embedding-3"}])

        responses = iter([create_resp, list_resp])
        with patch.object(httpx.Client, "request", side_effect=lambda *a, **kw: next(responses)):
            with Client("http://localhost:8000") as c:
                emb = c.embedding.create("text-embedding-3", ["hello", "world"])
                models = c.embedding.list_models()

        assert len(emb["data"]) == 1
        assert models[0]["model"] == "text-embedding-3"
        _pass("test_embedding_create_and_list")
    except Exception as e:
        _fail("test_embedding_create_and_list", e)


# ---------------------------------------------------------------------------
# Test 12: Memory CRUD
# ---------------------------------------------------------------------------

def test_memory_crud():
    try:
        list_resp = _make_response(200, [{"id": "m1", "content": "hello"}])
        get_resp = _make_response(200, {"id": "m1", "content": "hello"})
        create_resp = _make_response(201, {"id": "m2", "content": "world"})
        del_resp = _make_response(200, {"deleted": True})

        responses = iter([list_resp, get_resp, create_resp, del_resp])
        with patch.object(httpx.Client, "request", side_effect=lambda *a, **kw: next(responses)):
            with Client("http://localhost:8000", api_key="k") as c:
                lst = c.memory.list(tenant_id="t1")
                got = c.memory.get("m1")
                created = c.memory.create("t1", "world", metadata={"tag": "test"})
                deleted = c.memory.delete("m2")

        assert lst[0]["id"] == "m1"
        assert got["id"] == "m1"
        assert created["id"] == "m2"
        assert deleted["deleted"] is True
        _pass("test_memory_crud")
    except Exception as e:
        _fail("test_memory_crud", e)


# ---------------------------------------------------------------------------
# Test 13: Orchestrator create/list/execute/delete
# ---------------------------------------------------------------------------

def test_orchestrator_workflow():
    try:
        create_resp = _make_response(201, {"workflow_id": "wf1", "name": "My Workflow"})
        list_resp = _make_response(200, [{"workflow_id": "wf1"}])
        exec_resp = _make_response(200, {"run_id": "r1", "status": "running"})
        del_resp = _make_response(200, {"deleted": True})

        responses = iter([create_resp, list_resp, exec_resp, del_resp])
        with patch.object(httpx.Client, "request", side_effect=lambda *a, **kw: next(responses)):
            with Client("http://localhost:8000", api_key="admin") as c:
                wf = c.orchestrator.create_workflow({"name": "My Workflow", "steps": []})
                workflows = c.orchestrator.list_workflows()
                run = c.orchestrator.execute("wf1", inputs={"query": "test"})
                deleted = c.orchestrator.delete_workflow("wf1")

        assert wf["workflow_id"] == "wf1"
        assert workflows[0]["workflow_id"] == "wf1"
        assert run["run_id"] == "r1"
        assert deleted["deleted"] is True
        _pass("test_orchestrator_workflow")
    except Exception as e:
        _fail("test_orchestrator_workflow", e)


# ---------------------------------------------------------------------------
# Test 14: Exception mapping — 401 → AuthenticationError
# ---------------------------------------------------------------------------

def test_exception_401_auth_error():
    try:
        fake_resp = _make_response(401, {"detail": "Unauthorized"})
        with patch.object(httpx.Client, "request", return_value=fake_resp):
            with Client("http://localhost:8000", api_key="bad-key") as c:
                try:
                    c.chat.completions.create("gpt-4o", [])
                    _fail("test_exception_401_auth_error", AssertionError("No exception raised"))
                    return
                except AuthenticationError as exc:
                    assert exc.status_code == 401
        _pass("test_exception_401_auth_error")
    except Exception as e:
        _fail("test_exception_401_auth_error", e)


# ---------------------------------------------------------------------------
# Test 15: Exception mapping — 403 → AuthenticationError
# ---------------------------------------------------------------------------

def test_exception_403_auth_error():
    try:
        fake_resp = _make_response(403, {"detail": "Forbidden"})
        with patch.object(httpx.Client, "request", return_value=fake_resp):
            with Client("http://localhost:8000") as c:
                try:
                    c.memory.list()
                    _fail("test_exception_403_auth_error", AssertionError("No exception raised"))
                    return
                except AuthenticationError as exc:
                    assert exc.status_code == 403
        _pass("test_exception_403_auth_error")
    except Exception as e:
        _fail("test_exception_403_auth_error", e)


# ---------------------------------------------------------------------------
# Test 16: Exception mapping — 404 → NotFoundError
# ---------------------------------------------------------------------------

def test_exception_404_not_found():
    try:
        fake_resp = _make_response(404, {"detail": "not found"})
        with patch.object(httpx.Client, "request", return_value=fake_resp):
            with Client("http://localhost:8000") as c:
                try:
                    c.memory.get("does-not-exist")
                    _fail("test_exception_404_not_found", AssertionError("No exception raised"))
                    return
                except NotFoundError as exc:
                    assert exc.status_code == 404
        _pass("test_exception_404_not_found")
    except Exception as e:
        _fail("test_exception_404_not_found", e)


# ---------------------------------------------------------------------------
# Test 17: Exception mapping — 429 → RateLimitError
# ---------------------------------------------------------------------------

def test_exception_429_rate_limit():
    try:
        fake_resp = _make_response(429, {"detail": "Too Many Requests"})
        with patch.object(httpx.Client, "request", return_value=fake_resp):
            with Client("http://localhost:8000") as c:
                try:
                    c.embedding.create("m", ["a"])
                    _fail("test_exception_429_rate_limit", AssertionError("No exception raised"))
                    return
                except RateLimitError as exc:
                    assert exc.status_code == 429
        _pass("test_exception_429_rate_limit")
    except Exception as e:
        _fail("test_exception_429_rate_limit", e)


# ---------------------------------------------------------------------------
# Test 18: Exception mapping — 500 → APIError
# ---------------------------------------------------------------------------

def test_exception_500_api_error():
    try:
        fake_resp = _make_response(500, {"detail": "Internal Server Error"})
        with patch.object(httpx.Client, "request", return_value=fake_resp):
            with Client("http://localhost:8000") as c:
                try:
                    c.agent.list_sessions()
                    _fail("test_exception_500_api_error", AssertionError("No exception raised"))
                    return
                except APIError as exc:
                    assert exc.status_code == 500
                    assert not isinstance(exc, (AuthenticationError, NotFoundError, RateLimitError))
        _pass("test_exception_500_api_error")
    except Exception as e:
        _fail("test_exception_500_api_error", e)


# ---------------------------------------------------------------------------
# Test 19: AsyncClient context manager
# ---------------------------------------------------------------------------

async def _async_ctx_manager():
    async with AsyncClient("http://localhost:8000", api_key="k", tenant_id="t2") as c:
        assert c._api_key == "k"
        assert c._tenant_id == "t2"
    return True


def test_async_client_context_manager():
    try:
        ok = _run_async(_async_ctx_manager())
        assert ok is True
        _pass("test_async_client_context_manager")
    except Exception as e:
        _fail("test_async_client_context_manager", e)


# ---------------------------------------------------------------------------
# Test 20: base_url trailing slash normalisation
# ---------------------------------------------------------------------------

def test_base_url_trailing_slash():
    try:
        c = Client("http://localhost:8000/")
        assert c._base_url == "http://localhost:8000"
        c2 = Client("http://localhost:8000")
        assert c2._base_url == "http://localhost:8000"
        c.close()
        c2.close()
        _pass("test_base_url_trailing_slash")
    except Exception as e:
        _fail("test_base_url_trailing_slash", e)


# ---------------------------------------------------------------------------
# Test 21: AIPlatformError is base of all SDK exceptions
# ---------------------------------------------------------------------------

def test_exception_hierarchy():
    try:
        assert issubclass(APIError, AIPlatformError)
        assert issubclass(AuthenticationError, APIError)
        assert issubclass(NotFoundError, APIError)
        assert issubclass(RateLimitError, APIError)
        _pass("test_exception_hierarchy")
    except Exception as e:
        _fail("test_exception_hierarchy", e)


# ---------------------------------------------------------------------------
# Test 22: Resource property returns correct type
# ---------------------------------------------------------------------------

def test_resource_properties():
    try:
        from ai_platform_lab.resources.agent import AgentResource
        from ai_platform_lab.resources.chat import ChatResource
        from ai_platform_lab.resources.embedding import EmbeddingResource
        from ai_platform_lab.resources.memory import MemoryResource
        from ai_platform_lab.resources.orchestrator import OrchestratorResource
        from ai_platform_lab.resources.rag import RagResource

        c = Client("http://localhost:8000", api_key="x")
        assert isinstance(c.chat, ChatResource)
        assert isinstance(c.rag, RagResource)
        assert isinstance(c.agent, AgentResource)
        assert isinstance(c.embedding, EmbeddingResource)
        assert isinstance(c.memory, MemoryResource)
        assert isinstance(c.orchestrator, OrchestratorResource)
        c.close()
        _pass("test_resource_properties")
    except Exception as e:
        _fail("test_resource_properties", e)


# ---------------------------------------------------------------------------
# Test 23: Async resource properties return async types
# ---------------------------------------------------------------------------

def test_async_resource_properties():
    try:
        from ai_platform_lab.resources.agent import AsyncAgentResource
        from ai_platform_lab.resources.chat import AsyncChatResource
        from ai_platform_lab.resources.embedding import AsyncEmbeddingResource
        from ai_platform_lab.resources.memory import AsyncMemoryResource
        from ai_platform_lab.resources.orchestrator import AsyncOrchestratorResource
        from ai_platform_lab.resources.rag import AsyncRagResource

        ac = AsyncClient("http://localhost:8000", api_key="x")
        assert isinstance(ac.chat, AsyncChatResource)
        assert isinstance(ac.rag, AsyncRagResource)
        assert isinstance(ac.agent, AsyncAgentResource)
        assert isinstance(ac.embedding, AsyncEmbeddingResource)
        assert isinstance(ac.memory, AsyncMemoryResource)
        assert isinstance(ac.orchestrator, AsyncOrchestratorResource)
        _pass("test_async_resource_properties")
    except Exception as e:
        _fail("test_async_resource_properties", e)


# ---------------------------------------------------------------------------
# Test 24: Async memory CRUD
# ---------------------------------------------------------------------------

async def _async_memory():
    list_resp = _make_response(200, [{"id": "m1"}])
    create_resp = _make_response(201, {"id": "m2"})
    responses = iter([list_resp, create_resp])
    with patch.object(httpx.AsyncClient, "request", side_effect=lambda *a, **kw: responses.__next__()):
        async with AsyncClient("http://localhost:8000") as c:
            lst = await c.memory.list()
            created = await c.memory.create("t1", "data")
    return lst, created


def test_async_memory_crud():
    try:
        lst, created = _run_async(_async_memory())
        assert lst[0]["id"] == "m1"
        assert created["id"] == "m2"
        _pass("test_async_memory_crud")
    except Exception as e:
        _fail("test_async_memory_crud", e)


# ---------------------------------------------------------------------------
# Test 25: Timeout is forwarded to httpx.Client
# ---------------------------------------------------------------------------

def test_timeout_forwarded():
    try:
        c = Client("http://localhost:8000", timeout=5.0)
        assert c._http.timeout.read == 5.0
        c.close()
        _pass("test_timeout_forwarded")
    except Exception as e:
        _fail("test_timeout_forwarded", e)


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_version_export()
    test_client_init()
    test_client_context_manager()
    test_header_injection()
    test_no_api_key_no_auth_header()
    test_chat_completions_sync()
    test_chat_completions_async()
    test_rag_query()
    test_rag_list_kbs()
    test_agent_run_and_sessions()
    test_embedding_create_and_list()
    test_memory_crud()
    test_orchestrator_workflow()
    test_exception_401_auth_error()
    test_exception_403_auth_error()
    test_exception_404_not_found()
    test_exception_429_rate_limit()
    test_exception_500_api_error()
    test_async_client_context_manager()
    test_base_url_trailing_slash()
    test_exception_hierarchy()
    test_resource_properties()
    test_async_resource_properties()
    test_async_memory_crud()
    test_timeout_forwarded()

    total = PASS_COUNT + FAIL_COUNT
    print(f"\n{'='*50}")
    print(f"Results: {PASS_COUNT}/{total} passed")
    if FAIL_COUNT:
        sys.exit(1)
    print("All tests passed!")
