# ai-platform-lab

Python client for [AI Platform Lab](https://github.com/xingyun0812/ai-platform-lab) — chat, RAG, agent, memory, and orchestrator APIs.

## Install

```bash
# from PyPI (Phase N)
pip install ai-platform-lab

# local development
pip install -e "sdk/python[dev]"
```

## Quickstart

```python
from ai_platform_lab import Client

client = Client(
    base_url="http://127.0.0.1:8000",
    api_key="sk-tenant-admin-change-me",
    tenant_id="admin",
)

# Chat
resp = client.chat.completions.create(
    model="chat-fast",
    messages=[{"role": "user", "content": "hello"}],
    max_tokens=32,
)
print(resp["choices"][0]["message"]["content"])

# RAG
rag = client.rag.query(
    "RAG 数据管道",
    kb_id="lab-demo",
    tenant_id="admin",
    version=1,
    min_score=0.2,
)

# Agent
agent = client.agent.run(
    session_id="demo",
    message="1+1",
    tenant_id="admin",
    messages=[{"role": "user", "content": "1+1"}],
)

client.close()
```

Async client: `from ai_platform_lab import AsyncClient` (same constructor, `await` on resources).

## Environment

| Variable | Purpose |
|----------|---------|
| `SDK_BASE_URL` | Default gateway URL for smoke scripts |
| `DEMO_TENANT` / `DEMO_TOKEN` | Tenant id and API key for demos |

## Verify against a running gateway

```bash
pip install -e "sdk/python[dev]"
python eval/sdk_smoke.py --base-url http://127.0.0.1:8000
```

## License

MIT
