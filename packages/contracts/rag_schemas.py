from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

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


class IndexUploadResponse(BaseModel):
    task_id: str
    status: TaskStatus
    kb_id: str
    version: int
    source_uri: str
    saved_path: str
