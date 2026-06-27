"""HTTP API schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class OwnerCreateRequest(BaseModel):
    owner_id: str
    display_name: str = ""
    kind: str = "person"
    profile_json: dict[str, Any] = Field(default_factory=dict)
    settings_json: dict[str, Any] = Field(default_factory=dict)


class OwnerResponse(BaseModel):
    owner_id: str
    display_name: str
    kind: str
    status: str
    profile_json: dict[str, Any] = Field(default_factory=dict)
    settings_json: dict[str, Any] = Field(default_factory=dict)


class CompanionCreateRequest(BaseModel):
    companion_id: str
    owner_id: str
    display_name: str = ""
    kind: str = "companion"
    status: str = "active"
    profile_json: dict[str, Any] = Field(default_factory=dict)
    runtime_config_json: dict[str, Any] = Field(default_factory=dict)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class CompanionResponse(BaseModel):
    companion_id: str
    owner_id: str
    display_name: str
    kind: str
    status: str
    current_genome_id: str | None = None
    default_memory_realm_id: str | None = None
    profile_json: dict[str, Any] = Field(default_factory=dict)
    runtime_config_json: dict[str, Any] = Field(default_factory=dict)
    metadata_json: dict[str, Any] = Field(default_factory=dict)
