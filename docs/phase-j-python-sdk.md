# Phase J — Python SDK (`sdk/python/`)

> **Issue #29 / Roadmap #45** — A fully-typed, OpenAI-style Python SDK that wraps the AI Platform Lab REST API. External developers can `pip install ai-platform-lab` and immediately call `client.chat.completions.create(...)` without hand-crafting HTTP requests.

---

## Table of Contents

1. [Design Overview](#design-overview)
2. [Installation](#installation)
3. [Quick-start](#quick-start)
4. [Client Classes](#client-classes)
5. [Resources](#resources)
6. [Exception Hierarchy](#exception-hierarchy)
7. [Sync vs Async](#sync-vs-async)
8. [Configuration Reference](#configuration-reference)
9. [REST API Coverage Table](#rest-api-coverage-table)
10. [Test Section](#test-section)
11. [Code Navigation](#code-navigation)
12. [Known Limits](#known-limits)
13. [Integration Instructions (for parent agent)](#integration-instructions)
14. [Interview Talking Points](#interview-talking-points)

---

## Design Overview

```
sdk/python/
├── pyproject.toml                    # PEP 517/518 build config
└── ai_platform_lab/
    ├── py.typed                      # PEP 561 marker
    ├── __init__.py                   # exports Client, AsyncClient, __version__, exceptions
    ├── client.py                     # Client (sync) + AsyncClient (async)
    ├── _base.py                      # BaseResource + AsyncBaseResource helpers
    ├── exceptions.py                 # AIPlatformError hierarchy
    └── resources/
        ├── __init__.py
        ├── chat.py                   # ChatResource / AsyncChatResource
        ├── rag.py                    # RagResource / AsyncRagResource
        ├── agent.py                  # AgentResource / AsyncAgentResource
        ├── embedding.py              # EmbeddingResource / AsyncEmbeddingResource
        ├── memory.py                 # MemoryResource / AsyncMemoryResource
        └── orchestrator.py           # OrchestratorResource / AsyncOrchestratorResource
```

**Key design decisions:**

| Decision | Choice | Rationale |
|---|---|---|
| HTTP library | `httpx` | Ships both sync and async clients, mirrors `requests` API familiarity |
| Authentication | `Authorization: Bearer <api_key>` header | Standard Bearer token pattern |
| Multi-tenancy | `X-Tenant-Id` header | Matches gateway convention |
| Error mapping | status → specific exception class | Lets callers `except NotFoundError` precisely |
| No pydantic at runtime | Dict return values | Keeps SDK lightweight; callers can layer pydantic themselves |
| Resource instantiation | Property returns new resource per access | Stateless; always inherits latest client state |
| Separate sync/async classes | `ChatResource` / `AsyncChatResource` | Avoids `asyncio` import in sync path; cleaner typing |

---

## Installation

```bash
# From PyPI (when published)
pip install ai-platform-lab

# With optional pydantic models
pip install "ai-platform-lab[pydantic]"

# For development / testing
pip install "ai-platform-lab[dev]"

# From local checkout
cd sdk/python
pip install -e ".[dev]"
```

---

## Quick-start

### Sync client

```python
from ai_platform_lab import Client

with Client(base_url="http://localhost:8000", api_key="sk-xxx", tenant_id="my-tenant") as client:
    # Chat completion
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hello!"}],
    )
    print(resp["choices"][0]["message"]["content"])

    # RAG query
    results = client.rag.query("what is RAG?", kb_id="kb-main", top_k=5)

    # Memory
    client.memory.create("my-tenant", "User prefers dark mode", metadata={"source": "settings"})
```

### Async client

```python
import asyncio
from ai_platform_lab import AsyncClient

async def main():
    async with AsyncClient(base_url="http://localhost:8000", api_key="sk-xxx") as client:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Async hello!"}],
        )
        print(resp)

asyncio.run(main())
```

---

## Client Classes

### `Client(base_url, api_key=None, tenant_id=None, timeout=30.0)`

Synchronous client backed by `httpx.Client`.

| Attribute | Type | Description |
|---|---|---|
| `.chat` | `ChatResource` | Chat completion operations |
| `.rag` | `RagResource` | Retrieval-augmented generation |
| `.agent` | `AgentResource` | Agent session management |
| `.embedding` | `EmbeddingResource` | Text embeddings |
| `.memory` | `MemoryResource` | Persistent memory store |
| `.orchestrator` | `OrchestratorResource` | Workflow orchestration |
| `.close()` | — | Closes underlying HTTP client |

Implements `__enter__` / `__exit__` for context manager usage.

### `AsyncClient(base_url, api_key=None, tenant_id=None, timeout=30.0)`

Asynchronous client backed by `httpx.AsyncClient`. All resource methods are `async`. Implements `__aenter__` / `__aexit__`.

---

## Resources

### `chat.completions.create(model, messages, **kwargs)`

```python
resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hi"}],
    temperature=0.7,
)
```

### `rag.query(text, kb_id, top_k=5)`

```python
results = client.rag.query("document retrieval", kb_id="kb1")
```

### `rag.upload(kb_id, file_path)`

```python
resp = client.rag.upload("kb1", "/path/to/doc.pdf")
```

### `rag.list_kbs()`

```python
kbs = client.rag.list_kbs()
```

### `agent.run(session_id, message, tools=None)`

```python
reply = client.agent.run("sess-1", "Summarize this document", tools=["search"])
```

### `agent.list_sessions()` / `agent.get_session(session_id)`

### `embedding.create(model, texts)` / `embedding.list_models()`

### `memory.list(tenant_id=None)` / `memory.get(memory_id)` / `memory.create(tenant_id, content, metadata=None)` / `memory.delete(memory_id)`

### `orchestrator.create_workflow(workflow)` / `orchestrator.list_workflows()` / `orchestrator.execute(workflow_id, inputs)` / `orchestrator.delete_workflow(workflow_id)`

---

## Exception Hierarchy

```
AIPlatformError (base)
└── APIError (status_code, message, body)
    ├── AuthenticationError   → 401 / 403
    ├── NotFoundError         → 404
    └── RateLimitError        → 429
```

Usage:

```python
from ai_platform_lab import Client
from ai_platform_lab.exceptions import NotFoundError, RateLimitError, AuthenticationError, APIError

try:
    client.memory.get("does-not-exist")
except NotFoundError as e:
    print(f"Not found: {e.status_code} {e.message}")
except RateLimitError:
    time.sleep(1)
    # retry
except AuthenticationError:
    # refresh token
    ...
except APIError as e:
    print(f"Unexpected error: {e.status_code}")
```

---

## Sync vs Async

| Feature | `Client` | `AsyncClient` |
|---|---|---|
| HTTP backend | `httpx.Client` | `httpx.AsyncClient` |
| Method calls | `result = client.chat.completions.create(...)` | `result = await client.chat.completions.create(...)` |
| Context manager | `with Client(...) as c:` | `async with AsyncClient(...) as c:` |
| Best for | Scripts, CLI tools, Django (sync) | FastAPI, aiohttp, async frameworks |

Both clients expose identical resource structure — just add `await` for async.

---

## Configuration Reference

The SDK is a **standalone package** — no gateway `settings.py` changes needed.

| Constructor Parameter | Type | Default | Description |
|---|---|---|---|
| `base_url` | `str` | (required) | Gateway URL, e.g. `http://localhost:8000` |
| `api_key` | `str \| None` | `None` | Sent as `Authorization: Bearer <key>` |
| `tenant_id` | `str \| None` | `None` | Sent as `X-Tenant-Id` header |
| `timeout` | `float` | `30.0` | HTTP request timeout in seconds |

---

## REST API Coverage Table

| SDK Method | HTTP | Endpoint |
|---|---|---|
| `chat.completions.create()` | POST | `/v1/chat/completions` |
| `rag.query()` | POST | `/v1/rag/query` |
| `rag.upload()` | POST | `/v1/rag/{kb_id}/upload` |
| `rag.list_kbs()` | GET | `/v1/rag/kbs` |
| `agent.run()` | POST | `/v1/agent/run` |
| `agent.list_sessions()` | GET | `/v1/agent/sessions` |
| `agent.get_session()` | GET | `/v1/agent/sessions/{id}` |
| `embedding.create()` | POST | `/v1/embeddings` |
| `embedding.list_models()` | GET | `/v1/embeddings/models` |
| `memory.list()` | GET | `/v1/memory` |
| `memory.get()` | GET | `/v1/memory/{id}` |
| `memory.create()` | POST | `/v1/memory` |
| `memory.delete()` | DELETE | `/v1/memory/{id}` |
| `orchestrator.create_workflow()` | POST | `/v1/orchestrator/workflows` |
| `orchestrator.list_workflows()` | GET | `/v1/orchestrator/workflows` |
| `orchestrator.execute()` | POST | `/v1/orchestrator/workflows/{id}/execute` |
| `orchestrator.delete_workflow()` | DELETE | `/v1/orchestrator/workflows/{id}` |

---

## Test Section

### Running tests

```bash
# From repo root — no install needed
python3 tests/test_sdk.py

# With pytest
cd sdk/python && pip install -e ".[dev]" && pytest ../../tests/test_sdk.py -v
```

### Test coverage (25 test cases)

| # | Test Name | What it verifies |
|---|---|---|
| 1 | `test_version_export` | `__version__` is exported correctly |
| 2 | `test_client_init` | Constructor stores base_url, api_key, tenant_id |
| 3 | `test_client_context_manager` | `with Client(...)` calls close on exit |
| 4 | `test_header_injection` | Authorization + X-Tenant-Id injected |
| 5 | `test_no_api_key_no_auth_header` | Missing headers when no api_key/tenant_id |
| 6 | `test_chat_completions_sync` | Sync POST /v1/chat/completions |
| 7 | `test_chat_completions_async` | Async POST /v1/chat/completions |
| 8 | `test_rag_query` | POST /v1/rag/query |
| 9 | `test_rag_list_kbs` | GET /v1/rag/kbs |
| 10 | `test_agent_run_and_sessions` | run + list_sessions + get_session |
| 11 | `test_embedding_create_and_list` | create embeddings + list models |
| 12 | `test_memory_crud` | list + get + create + delete |
| 13 | `test_orchestrator_workflow` | create + list + execute + delete |
| 14 | `test_exception_401_auth_error` | 401 → `AuthenticationError` |
| 15 | `test_exception_403_auth_error` | 403 → `AuthenticationError` |
| 16 | `test_exception_404_not_found` | 404 → `NotFoundError` |
| 17 | `test_exception_429_rate_limit` | 429 → `RateLimitError` |
| 18 | `test_exception_500_api_error` | 500 → `APIError` (not subclass) |
| 19 | `test_async_client_context_manager` | `async with AsyncClient(...)` |
| 20 | `test_base_url_trailing_slash` | Trailing slash normalisation |
| 21 | `test_exception_hierarchy` | All exceptions inherit from `AIPlatformError` |
| 22 | `test_resource_properties` | Each property returns correct type |
| 23 | `test_async_resource_properties` | Async properties return async types |
| 24 | `test_async_memory_crud` | Async memory list + create |
| 25 | `test_timeout_forwarded` | Timeout forwarded to httpx.Client |

---

## Code Navigation

| File | Purpose |
|---|---|
| `sdk/python/pyproject.toml` | Build system, dependencies, dev extras |
| `sdk/python/ai_platform_lab/__init__.py` | Public API surface |
| `sdk/python/ai_platform_lab/client.py` | `Client` + `AsyncClient` entry points |
| `sdk/python/ai_platform_lab/_base.py` | `BaseResource` + `AsyncBaseResource` + error mapping |
| `sdk/python/ai_platform_lab/exceptions.py` | Exception hierarchy |
| `sdk/python/ai_platform_lab/resources/chat.py` | Chat completions |
| `sdk/python/ai_platform_lab/resources/rag.py` | RAG operations |
| `sdk/python/ai_platform_lab/resources/agent.py` | Agent sessions |
| `sdk/python/ai_platform_lab/resources/embedding.py` | Embeddings |
| `sdk/python/ai_platform_lab/resources/memory.py` | Memory CRUD |
| `sdk/python/ai_platform_lab/resources/orchestrator.py` | Workflow orchestration |
| `tests/test_sdk.py` | 25 unit tests, mocked via `unittest.mock.patch` |

---

## Known Limits

1. **No automatic retry / exponential backoff** — callers must implement retry logic around `RateLimitError`. A future `max_retries` constructor param could add this.
2. **No streaming support** — `chat.completions.create()` returns the full response. Server-Sent Events (SSE) streaming requires a separate `stream=True` code path with `iter_lines()`.
3. **No file upload streaming** — `rag.upload()` reads the entire file into memory before sending. Large files should be chunked server-side or use a pre-signed URL flow.
4. **No WebSocket support** — real-time agent interactions that require persistent bidirectional channels are not covered.
5. **No pydantic response models** — all responses are plain `dict`. There are no typed response objects (`ChatCompletion`, `EmbeddingResponse`, etc.), so callers must access keys manually.
6. **No pagination helpers** — list endpoints return the raw array; cursor/offset pagination must be handled by the caller.
7. **No connection pooling configuration** — `httpx.Client` uses default pool settings; high-throughput scenarios may need custom `limits=httpx.Limits(...)`.
8. **No middleware / interceptor hooks** — cannot inject request/response middleware (e.g., logging, tracing) without subclassing.

---

## Integration Instructions

> The SDK is a **standalone package** — no changes to gateway files are required.

### README.md — add section

```markdown
## Python SDK

```bash
cd sdk/python && pip install -e .
```

```python
from ai_platform_lab import Client
client = Client("http://localhost:8000", api_key="sk-xxx")
resp = client.chat.completions.create(model="gpt-4o", messages=[{"role":"user","content":"Hi"}])
```

See `docs/phase-j-python-sdk.md` for full documentation.
```

### `.env.example` — no new vars needed

SDK is configured at runtime via constructor arguments, not environment variables.

### `apps/gateway/main.py` — no changes needed

SDK is consumed by external apps, not integrated into the gateway.

### `apps/gateway/settings.py` — no changes needed

No new settings fields required.

### `docs/roadmap.md` — add entry

```markdown
| J  | #29  | Python SDK                    | `sdk/python/`                     | Client, AsyncClient, 6 resources, exceptions | ✅ Done |
```

---

## Interview Talking Points

1. **OpenAI SDK design pattern** — The SDK mirrors OpenAI's Python SDK: a top-level `Client` exposes resource namespaces (`.chat`, `.rag`, `.memory`), each resource has methods that map to single REST endpoints. This gives developers an instantly familiar API.

2. **Sync and async parity via httpx** — Using `httpx` instead of `requests` gives first-class async support with a single code path. `BaseResource` and `AsyncBaseResource` share identical logic differing only on `await`. This avoids duplicating request/error handling.

3. **Centralised error mapping** — `_raise_for_response()` in `_base.py` maps HTTP status codes to typed exceptions. Callers can `except NotFoundError` rather than checking `resp.status_code == 404`. This is the same pattern used by stripe-python and openai-python.

4. **PEP 561 compliance** — `py.typed` marker tells type checkers (mypy, pyright) that the package ships inline type annotations, enabling full static analysis for downstream code.

5. **Zero gateway coupling** — The SDK imports nothing from `apps.gateway` or `packages.*`. It works against any server implementing the same REST contract, making it testable without starting the gateway (HTTP mocking via `unittest.mock.patch`).

6. **Resource-per-access property pattern** — Each resource property (`@property`) instantiates a fresh resource passing the current client credentials. This means changing `client._api_key` mid-session automatically propagates to all future resource calls — no stale credential risk.

7. **Python 3.9+ compatibility** — All files start with `from __future__ import annotations` so PEP 604 union types (`X | Y`) work on Python 3.9 without `from typing import Union`. Avoids `datetime.UTC` (added in 3.11) and other newer APIs.

8. **Graceful test isolation** — Tests patch `httpx.Client.request` via `unittest.mock.patch` — no `respx` dependency needed. This makes tests runnable in any environment that has `httpx` installed.
