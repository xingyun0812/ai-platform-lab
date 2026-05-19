from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.rag.paths import resolve_source_path
from apps.gateway.rag.pipeline import resolve_retrieve_version, run_index_task, task_store
from apps.gateway.settings import get_settings
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.contracts.rag_schemas import (
    IndexJobRequest,
    IndexJobResponse,
    IndexTaskView,
    IndexUploadResponse,
    KbVersionsResponse,
    RetrieveRequest,
    RetrieveResponse,
    RetrievedChunk,
    TaskStatus,
)
from packages.rag.embeddings import embed_texts
from packages.rag.vector_store import VectorStore

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
    background_tasks.add_task(run_index_task, record.task_id)
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
    background_tasks.add_task(run_index_task, record.task_id)
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

    try:
        resolved_version = resolve_retrieve_version(body.kb_id, body.version)
        query_vectors = await embed_texts([body.query])
        store = VectorStore()
        hits = store.retrieve(
            kb_id=body.kb_id,
            version=resolved_version,
            query_vector=query_vectors[0],
            top_k=body.top_k,
        )
    except ValueError as e:
        return json_error(404, "KB_NOT_FOUND", str(e))
    except Exception as e:
        logger.exception("retrieve failed kb_id=%s", body.kb_id)
        return json_error(503, "RETRIEVE_ERROR", str(e))

    chunks: list[RetrievedChunk] = []
    for hit in hits:
        chunk_id = hit.get("chunk_id")
        version = hit.get("version")
        source_uri = hit.get("source_uri")
        text = hit.get("text")
        if not isinstance(chunk_id, str) or not isinstance(version, int):
            continue
        if not isinstance(source_uri, str) or not isinstance(text, str):
            continue
        offset = hit.get("offset")
        chunks.append(
            RetrievedChunk(
                chunk_id=chunk_id,
                kb_id=str(hit.get("kb_id", body.kb_id)),
                version=version,
                source_uri=source_uri,
                offset=int(offset) if isinstance(offset, int) else 0,
                text=text,
                score=float(hit.get("score", 0.0)),
            )
        )

    return RetrieveResponse(
        kb_id=body.kb_id,
        version=resolved_version,
        query=body.query,
        chunks=chunks,
    )
