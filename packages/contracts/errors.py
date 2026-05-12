from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str
    message: str
    trace_id: str | None = None
    detail: dict[str, Any] | None = None


class ErrorBody(BaseModel):
    error: ErrorDetail = Field(..., description="统一错误体")
