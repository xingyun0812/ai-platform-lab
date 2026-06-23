from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.rag.paths import resolve_source_path
from apps.gateway.rag.pipeline import (
    _kb_routing_rules,
    _list_kb_versions,
    resolve_query_version,
    run_index_task,
    task_store,
)
from apps.gateway.settings import get_settings
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.contracts.rag_schemas import (
    IndexJobRequest,
    IndexJobResponse,
    IndexTaskView,
    IndexUploadResponse,
    KbRoutingResponse,
    KbVersionsResponse,
    PurgeSourceRequest,
    PurgeSourceResponse,
    RetrieveRequest,
    RetrieveResponse,
)
from packages.rag.source_index import purge_source_index
from packages.rag.rerank import rerank_chunks
from packages.rag.rerank_providers import provider_config_from_settings
from packages.rag.retrieval import retrieve_chunks
from packages.rag.routing import describe_routing
from packages.rag.vector_store import VectorStore
from packages.tasks.queue import get_index_task_queue

logger = logging.getLogger("ai_platform.gateway.rag")

router = APIRouter(prefix="/internal", tags=["rag-internal"])


def _require_tenant(
    x_tenant_id: str | None,
    authorization: str | None,
    tenants: dict[str, TenantRecord],
) -> TenantRecord | JSONResponse:
    try:
        return resolve_tenant(x_tenant_id, authorization, tenants)
    except HTTPException as e:
        return json_error(int(e.status_code), "UNAUTHORIZED", str(e.detail))


def _dispatch_index(task_id: str, background_tasks: BackgroundTasks) -> None:
    queue = get_index_task_queue()
    if queue is not None:
        queue.enqueue(task_id)
        return
    background_tasks.add_task(run_index_task, task_id)


def _task_view(record) -> IndexTaskView:
    return IndexTaskView(
        task_id=record.task_id,
        status=record.status,
        kb_id=record.kb_id,
        version=record.version,
        source_uri=record.source_uri,
        error=record.error,
        chunks_indexed=record.chunks_indexed,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.post("/index", response_model=IndexJobResponse)
async def create_index_job(
    body: IndexJobRequest,
    background_tasks: BackgroundTasks,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant

    settings = get_settings()
    if not (settings.llm_api_key or "").strip():
        return json_error(
            503,
            "UPSTREAM_NOT_CONFIGURED",
            "LLM_API_KEY 未配置，无法 embedding",
        )

    try:
        resolve_source_path(body.source_uri)
    except ValueError as e:
        return json_error(400, "BAD_REQUEST", str(e))

    record = task_store.create(
        kb_id=body.kb_id,
        version=body.version,
        source_uri=body.source_uri,
    )
    _dispatch_index(record.task_id, background_tasks)
    return IndexJobResponse(
        task_id=record.task_id,
        status=record.status,
        kb_id=record.kb_id,
        version=record.version,
        source_uri=record.source_uri,
    )


@router.post("/index/upload", response_model=IndexUploadResponse)
async def upload_and_index(
    background_tasks: BackgroundTasks,
    kb_id: Annotated[str, Form()],
    version: Annotated[int, Form(ge=1)],
    file: UploadFile = File(...),
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant

    settings = get_settings()
    if not (settings.llm_api_key or "").strip():
        return json_error(503, "UPSTREAM_NOT_CONFIGURED", "LLM_API_KEY 未配置")

    safe_name = (file.filename or "upload.txt").replace("/", "_").replace("\\", "_")
    source_uri = f"uploads/{kb_id}/v{version}/{safe_name}"
    try:
        dest = resolve_source_path(source_uri)
    except ValueError as e:
        return json_error(400, "BAD_REQUEST", str(e))

    dest.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    dest.write_bytes(content)

    record = task_store.create(kb_id=kb_id, version=version, source_uri=source_uri)
    _dispatch_index(record.task_id, background_tasks)
    return IndexUploadResponse(
        task_id=record.task_id,
        status=record.status,
        kb_id=kb_id,
        version=version,
        source_uri=source_uri,
        saved_path=str(dest),
    )


@router.get("/index/tasks/{task_id}", response_model=IndexTaskView)
async def get_index_task(
    task_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant

    record = task_store.get(task_id)
    if not record:
        return json_error(404, "NOT_FOUND", f"任务不存在: {task_id}")
    return _task_view(record)


@router.post("/index/purge-source", response_model=PurgeSourceResponse)
async def purge_index_source(
    body: PurgeSourceRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant

    try:
        result = purge_source_index(
            kb_id=body.kb_id,
            version=body.version,
            source_uri=body.source_uri,
            delete_file=body.delete_file,
        )
    except Exception as e:
        logger.exception("purge source failed kb_id=%s source=%s", body.kb_id, body.source_uri)
        return json_error(503, "PURGE_SOURCE_ERROR", str(e))

    return PurgeSourceResponse(
        kb_id=body.kb_id,
        version=body.version,
        source_uri=body.source_uri,
        deleted_vectors=int(result["deleted_vectors"]),
        bm25_docs_remaining=int(result["bm25_docs_remaining"]),
        file_deleted=bool(result["file_deleted"]),
    )


@router.get("/kb/{kb_id}/versions", response_model=KbVersionsResponse)
async def list_kb_versions(
    kb_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant

    try:
        store = VectorStore()
        versions = store.list_versions(kb_id)
    except Exception as e:
        return json_error(503, "VECTOR_STORE_ERROR", str(e))

    latest = max(versions) if versions else None
    return KbVersionsResponse(kb_id=kb_id, versions=versions, latest=latest)


@router.get("/kb/{kb_id}/routing", response_model=KbRoutingResponse)
async def get_kb_routing(
    kb_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant

    try:
        info = describe_routing(
            kb_id,
            rules=_kb_routing_rules(),
            list_versions=_list_kb_versions,
        )
    except Exception as e:
        return json_error(503, "VECTOR_STORE_ERROR", str(e))

    return KbRoutingResponse(**info)


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    body: RetrieveRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant

    settings = get_settings()
    if not (settings.llm_api_key or "").strip():
        return json_error(503, "UPSTREAM_NOT_CONFIGURED", "LLM_API_KEY 未配置")

    tenant_id = tenant.tenant_id

    def _resolve(kb: str, ver: int | None) -> int:
        resolved, _, _ = resolve_query_version(
            kb,
            ver,
            tenant_id=tenant_id,
            query=body.query,
        )
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
        logger.exception("retrieve failed kb_id=%s", body.kb_id)
        return json_error(503, "RETRIEVE_ERROR", str(e))

    if settings.rag_rerank_enabled and chunks:
        rerank_cfg = provider_config_from_settings(settings)
        chunks, _ = rerank_chunks(
            body.query,
            chunks,
            top_n=settings.rag_rerank_top_n,
            mode=settings.rag_rerank_mode,
            provider_config=rerank_cfg,
        )

    return RetrieveResponse(
        kb_id=body.kb_id,
        version=resolved_version,
        query=body.query,
        chunks=chunks,
    )
