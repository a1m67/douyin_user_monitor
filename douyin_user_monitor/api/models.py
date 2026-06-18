from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field


class ResponseModel(BaseModel):
    code: int = 200
    router: str
    data: Any


class ErrorResponseModel(BaseModel):
    code: int = 400
    message: str
    router: str
    params: Dict[str, Any] = Field(default_factory=dict)
