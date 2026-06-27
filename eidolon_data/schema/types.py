"""Public service types used by ports and domain services."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MemoryItem(BaseModel):
    memory_id: str
    realm_id: str
    item_type: str
    content_summary: str = ""
    privacy: str = "normal"
    importance: int = Field(3, ge=1, le=5)
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


class MemoryIngestResult(BaseModel):
    memory_id: str
    realm_id: str
    engine: str
    external_key: str | None = None
    degraded: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecallOptions(BaseModel):
    top_k: int = Field(8, ge=1, le=100)
    filters: dict[str, Any] = Field(default_factory=dict)


class RecallHit(BaseModel):
    memory_id: str
    score: float = 0.0
    content: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecallResult(BaseModel):
    hits: list[RecallHit] = Field(default_factory=list)
    degraded: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryEngineHealth(BaseModel):
    ok: bool
    engine: str
    detail: dict[str, Any] = Field(default_factory=dict)
