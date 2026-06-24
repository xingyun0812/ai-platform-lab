"""Console V2 适配 API — 为 React 管理台提供 JSON 接口。"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.rag.pipeline import _kb_routing_rules, _list_kb_versions, resolve_query_version
from apps.gateway.rag.routes import _dispatch_index
from apps.gateway.rag.routes import _require_tenant as rag_require_tenant
from apps.gateway.rag.task_store import get_task_store as get_rag_task_store
from apps.gateway.settings import REPO_ROOT, get_settings
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_patch_tenant_limits
from packages.observability.metrics import get_metrics_store
from packages.rag.retrieval import retrieve_chunks

router = APIRouter(prefix="/internal", tags=["console"])
rag_router = APIRouter(prefix="/internal/rag", tags=["console-rag"])

_CONSOLE_KBS_PATH = REPO_ROOT / "data" / "console_kbs.json"


def _resolve(x_tenant_id: str | None, authorization: str | None) -> TenantRecord | JSONResponse:
    tenants = load_tenants()
    try:
        return resolve_tenant(x_tenant_id, authorization, tenants)
    except HTTPException as e:
        return json_error(int(e.status_code), "UNAUTHORIZED", str(e.detail))


def _require_admin(tenant: TenantRecord) -> JSONResponse | None:
    if not can_patch_tenant_limits(tenant.role):
        return json_error(403, "FORBIDDEN", "需要 platform_admin 角色")
    return None


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
    return slug or "kb"


def _load_console_kbs() -> dict[str, dict[str, Any]]:
    if not _CONSOLE_KBS_PATH.is_file():
        return {}
    try:
        data = json.loads(_CONSOLE_KBS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_console_kbs(data: dict[str, dict[str, Any]]) -> None:
    _CONSOLE_KBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONSOLE_KBS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _kb_ids() -> set[str]:
    ids = set(_kb_routing_rules().keys()) | set(_load_console_kbs().keys()) | {"lab-demo"}
    return {k for k in ids if k}


def _document_count(kb_id: str) -> int:
    settings = get_settings()
    root = settings.rag_data_root
    count = 0
    for pattern in ("samples/*.txt", f"uploads/{kb_id}/**/*"):
        count += len(list(root.glob(pattern)))
    return count


@router.get("/tenants")
async def list_tenants_for_console(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    caller = _resolve(x_tenant_id, authorization)
    if isinstance(caller, JSONResponse):
        return caller
    if not can_patch_tenant_limits(caller.role):
        return json_error(403, "FORBIDDEN", "需要 platform_admin 查看租户列表")

    tenants = load_tenants()
    rows: list[dict[str, Any]] = []
    for tid, record in sorted(tenants.items()):
        quota = record.token_budget_monthly if record.token_budget_monthly > 0 else 10_000_000
        rows.append(
            {
                "tenant_id": tid,
                "role": record.role,
                "quota_tokens_per_month": quota,
                "tokens_used_this_month": 0,
                "enabled": True,
                "created_at": "2024-01-01T00:00:00Z",
                "daily_request_quota": record.daily_request_quota,
                "token_budget_daily": record.token_budget_daily,
            }
        )
    return JSONResponse(rows)


@router.get("/metrics")
async def console_metrics(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    snapshot = get_metrics_store().dashboard_snapshot()
    settings = get_settings()

    tokens_today = 0
    tokens_by_tenant: list[dict[str, Any]] = []
    try:
        from packages.billing.db import get_billing_store

        store = get_billing_store(settings.database_url)
        if store is not None:
            since = datetime.now(UTC) - timedelta(hours=24)
            rows = store.aggregate_by_tenant(since=since, tenant_id=None)
            for row in rows:
                total = int(row.get("total_tokens") or 0)
                tokens_today += total
                tokens_by_tenant.append(
                    {"tenant_id": row.get("tenant_id", "unknown"), "tokens": total}
                )
    except Exception:
        pass

    if not tokens_by_tenant:
        tenants = load_tenants()
        for tid in tenants:
            tokens_by_tenant.append({"tenant_id": tid, "tokens": 0})

    return JSONResponse(
        {
            **snapshot,
            "tokens_today": tokens_today or snapshot.get("tokens_today", 0),
            "tokens_by_tenant": tokens_by_tenant,
        }
    )


@router.get("/settings")
async def console_settings(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    settings = get_settings()
    return JSONResponse(
        {
            "sandbox_enabled": settings.sandbox_enabled,
            "oauth2_enabled": settings.oauth2_enabled,
            "mtls_enabled": settings.mtls_enabled,
            "pii_enabled": settings.pii_service_enabled,
            "audit_enabled": True,
            "memory_enabled": settings.memory_store_enabled,
            "rag_enabled": True,
            "embedding_enabled": settings.embedding_service_enabled,
            "orchestrator_enabled": settings.orchestrator_enabled,
            "multi_agent_enabled": settings.multi_agent_enabled,
            "default_model": settings.default_model,
            "max_tokens_per_request": 4096,
            "rate_limit_per_minute": int(settings.default_rate_limit_rps * 60),
            "version": settings.app_version,
        }
    )


class ConsoleTokenRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)


@router.post("/auth/token")
async def console_auth_token(body: ConsoleTokenRequest) -> JSONResponse:
    """Console 登录：校验 tenants.yaml 中的 bearer_token。"""
    tenants = load_tenants()
    record = tenants.get(body.tenant_id)
    if record is None:
        return json_error(401, "UNAUTHORIZED", "租户不存在")
    if body.api_key.strip() != record.bearer_token:
        return json_error(401, "UNAUTHORIZED", "API Key 无效")
    return JSONResponse(
        {
            "token": record.bearer_token,
            "tenant_id": body.tenant_id,
            "role": record.role,
            "expires_at": None,
        }
    )


@rag_router.get("/knowledge-bases")
async def list_knowledge_bases(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    extra = _load_console_kbs()
    items: list[dict[str, Any]] = []
    for kb_id in sorted(_kb_ids()):
        meta = extra.get(kb_id, {})
        name = str(meta.get("name") or kb_id)
        try:
            versions = _list_kb_versions(kb_id)
        except Exception:
            versions = []
        items.append(
            {
                "kb_id": kb_id,
                "name": name,
                "description": meta.get("description") or "",
                "document_count": max(_document_count(kb_id), len(versions)),
                "created_at": meta.get("created_at") or "2024-01-01T00:00:00Z",
                "versions": versions,
            }
        )
    return JSONResponse(items)


class CreateKbRequest(BaseModel):
    name: str = Field(..., min_length=1)
    description: str = ""


@rag_router.post("/knowledge-bases")
async def create_knowledge_base(
    body: CreateKbRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    denied = _require_admin(tenant)
    if denied is not None:
        return denied

    kb_id = _slugify(body.name)
    existing = _load_console_kbs()
    if kb_id in existing:
        suffix = 2
        while f"{kb_id}-{suffix}" in existing:
            suffix += 1
        kb_id = f"{kb_id}-{suffix}"

    payload = {
        "name": body.name,
        "description": body.description,
        "created_at": datetime.now(UTC).isoformat(),
    }
    existing[kb_id] = payload
    _save_console_kbs(existing)
    return JSONResponse(
        {
            "kb_id": kb_id,
            "name": body.name,
            "description": body.description,
            "document_count": 0,
            "created_at": payload["created_at"],
        },
        status_code=201,
    )


@rag_router.delete("/knowledge-bases/{kb_id}")
async def delete_knowledge_base(
    kb_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    denied = _require_admin(tenant)
    if denied is not None:
        return denied

    existing = _load_console_kbs()
    if kb_id in existing:
        del existing[kb_id]
        _save_console_kbs(existing)
    return JSONResponse({"kb_id": kb_id, "deleted": True})


@rag_router.get("/knowledge-bases/{kb_id}/documents")
async def list_kb_documents(
    kb_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    settings = get_settings()
    root = settings.rag_data_root
    docs: list[dict[str, Any]] = []
    patterns = ["samples/*"] if kb_id == "lab-demo" else []
    patterns.append(f"uploads/{kb_id}/**/*")
    seen: set[str] = set()
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            doc_id = rel.replace("/", "_")
            if doc_id in seen:
                continue
            seen.add(doc_id)
            docs.append(
                {
                    "doc_id": doc_id,
                    "kb_id": kb_id,
                    "filename": path.name,
                    "status": "ready",
                    "chunk_count": 0,
                    "created_at": datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(),
                    "source_uri": rel,
                }
            )
    return JSONResponse(docs)


@rag_router.post("/knowledge-bases/{kb_id}/documents")
async def upload_kb_document(
    kb_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    version: Annotated[int, Form()] = 1,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenants = load_tenants()
    tenant = rag_require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant

    safe_name = (file.filename or "upload.txt").replace("/", "_").replace("\\", "_")
    source_uri = f"uploads/{kb_id}/v{version}/{safe_name}"
    from apps.gateway.rag.paths import resolve_source_path

    try:
        dest = resolve_source_path(source_uri)
    except ValueError as e:
        return json_error(400, "BAD_REQUEST", str(e))

    dest.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    dest.write_bytes(content)

    task_store = get_rag_task_store()
    record = task_store.create(kb_id=kb_id, version=version, source_uri=source_uri)
    _dispatch_index(record.task_id, background_tasks)
    return JSONResponse(
        {
            "doc_id": source_uri.replace("/", "_"),
            "status": str(record.status.value),
            "message": f"已创建索引任务 {record.task_id}",
            "task_id": record.task_id,
            "source_uri": source_uri,
        },
        status_code=201,
    )


@rag_router.delete("/knowledge-bases/{kb_id}/documents/{doc_id}")
async def delete_kb_document(
    kb_id: str,
    doc_id: str,
    version: int = 1,
    delete_file: bool = True,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    denied = _require_admin(tenant)
    if denied is not None:
        return denied

    settings = get_settings()
    root = settings.rag_data_root
    source_uri: str | None = None
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.replace("/", "_") == doc_id:
            source_uri = rel
            break
    if source_uri is None:
        return json_error(404, "NOT_FOUND", f"文档不存在: {doc_id}")

    from packages.rag.index_metrics import get_index_metrics
    from packages.rag.source_index import purge_source_index

    try:
        result = purge_source_index(
            kb_id=kb_id,
            version=version,
            source_uri=source_uri,
            delete_file=delete_file,
        )
        get_index_metrics().record_purge(kb_id=kb_id, version=version)
    except Exception as e:
        return json_error(503, "PURGE_SOURCE_ERROR", str(e))

    return JSONResponse(
        {
            "kb_id": kb_id,
            "doc_id": doc_id,
            "source_uri": source_uri,
            "deleted": True,
            "deleted_vectors": result["deleted_vectors"],
            "bm25_docs_remaining": result["bm25_docs_remaining"],
            "file_deleted": result["file_deleted"],
        }
    )


class RagQueryBody(BaseModel):
    query: str = Field(..., min_length=1)
    kb_id: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    version: int | None = None


@rag_router.post("/query")
async def console_rag_query(
    body: RagQueryBody,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenants = load_tenants()
    tenant = rag_require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant

    settings = get_settings()
    if not (settings.llm_api_key or "").strip():
        return json_error(503, "UPSTREAM_NOT_CONFIGURED", "LLM_API_KEY 未配置")

    tenant_id = tenant.tenant_id

    def _resolve(kb: str, ver: int | None) -> int:
        resolved, _, _ = resolve_query_version(kb, ver, tenant_id=tenant_id, query=body.query)
        return resolved

    try:
        resolved_version, chunks, _ = await retrieve_chunks(
            kb_id=body.kb_id,
            version=body.version,
            query=body.query,
            top_k=body.top_k,
            resolve_version=_resolve,
        )
    except ValueError as e:
        return json_error(404, "RAG_KB_NOT_FOUND", str(e))
    except Exception as e:
        return json_error(503, "RETRIEVE_ERROR", str(e))

    return JSONResponse(
        {
            "kb_id": body.kb_id,
            "version": resolved_version,
            "chunks": [
                {
                    "chunk_id": c.chunk_id,
                    "text": c.text,
                    "score": c.score,
                    "doc_id": c.source_uri,
                    "metadata": c.metadata,
                }
                for c in chunks
            ],
            "latency_ms": 0,
        }
    )
