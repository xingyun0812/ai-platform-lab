from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"


class IndexJobRequest(BaseModel):
    kb_id: str = Field(..., min_length=1, description="知识库 ID")
    version: int = Field(..., ge=1, description="版本号，手动 bump")
    source_uri: str = Field(
        ...,
        min_length=1,
        description="相对 RAG_DATA_ROOT 的文件路径，如 samples/hello.txt",
    )


class IndexJobResponse(BaseModel):
    task_id: str
    status: TaskStatus
    kb_id: str
    version: int
    source_uri: str


class IndexTaskView(BaseModel):
    task_id: str
    status: TaskStatus
    kb_id: str
    version: int
    source_uri: str
    error: str | None = None
    chunks_indexed: int | None = None
    created_at: datetime
    updated_at: datetime


class PurgeSourceRequest(BaseModel):
    kb_id: str = Field(..., min_length=1)
    version: int = Field(..., ge=1)
    source_uri: str = Field(..., min_length=1)
    delete_file: bool = Field(default=False, description="是否同时删除 RAG_DATA_ROOT 下源文件")


class PurgeSourceResponse(BaseModel):
    kb_id: str
    version: int
    source_uri: str
    deleted_vectors: int
    bm25_docs_remaining: int
    file_deleted: bool


class RetrieveRequest(BaseModel):
    kb_id: str = Field(..., min_length=1)
    version: int | None = Field(default=None, description="省略则使用已索引的最新版本")
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)


class RetrievedChunk(BaseModel):
    chunk_id: str
    kb_id: str
    version: int
    source_uri: str
    offset: int
    text: str
    score: float


class RetrieveResponse(BaseModel):
    kb_id: str
    version: int
    query: str
    chunks: list[RetrievedChunk]


class KbVersionsResponse(BaseModel):
    kb_id: str
    versions: list[int]
    latest: int | None = None


class KbRoutingResponse(BaseModel):
    kb_id: str
    indexed_versions: list[int]
    stable_version: int | None = None
    canary_version: int | None = None
    canary_percent: int = 0
    rollback_hint: str = ""


class IndexUploadResponse(BaseModel):
    task_id: str
    status: TaskStatus
    kb_id: str
    version: int
    source_uri: str
    saved_path: str


class RagQueryRequest(BaseModel):
    """第 3 周对外 RAG 问答；tenant_id 须与请求头 X-Tenant-Id 一致。"""

    tenant_id: str = Field(..., min_length=1)
    kb_id: str = Field(..., min_length=1)
    version: int | None = Field(default=None, description="省略则使用最新已索引版本")
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    min_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="相关度阈值，低于此分的片段不送入 LLM",
    )
    model: str | None = Field(default=None, description="覆盖默认 RAG 回答模型")


class RagCitation(BaseModel):
    chunk_id: str
    kb_id: str
    version: int
    source_uri: str
    score: float


class RagQueryTimings(BaseModel):
    retrieve_ms: float
    llm_ms: float
    total_ms: float
    retrieve_vector_ms: float | None = None
    retrieve_bm25_ms: float | None = None
    fusion_ms: float | None = None
    rerank_ms: float | None = None


class RagQueryResponse(BaseModel):
    tenant_id: str
    kb_id: str
    version: int
    query: str
    answer: str
    citations: list[RagCitation]
    timings: RagQueryTimings
    model: str
    min_score: float
    trace_id: str | None = None
