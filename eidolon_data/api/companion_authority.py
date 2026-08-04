"""Versioned, least-privilege Companion identity authority service."""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from eidolon_data import DataSettings, DataStore, load_settings


class CompanionIdentityResponse(BaseModel):
    """Stable identity subset consumed by OS control-plane services."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["companion.identity"] = "companion.identity"
    companion_id: str = Field(min_length=1, max_length=64)
    owner_id: str = Field(min_length=1, max_length=64)
    lifecycle_state: Literal["active", "inactive"]


def _required_service_token(configured: str | None) -> str:
    token = (configured or os.environ.get("EIDOLON_DATA_COMPANION_AUTHORITY_TOKEN") or "").strip()
    if len(token) < 24:
        raise RuntimeError(
            "EIDOLON_DATA_COMPANION_AUTHORITY_TOKEN must contain at least 24 characters"
        )
    return token


def _authorize(authorization: str | None, expected_token: str) -> None:
    authorization = authorization or ""
    scheme, separator, supplied_token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not supplied_token:
        raise HTTPException(status_code=401, detail="Bearer service credential required")
    if not hmac.compare_digest(supplied_token, expected_token):
        raise HTTPException(status_code=403, detail="invalid service credential")


def create_app(
    settings: DataSettings | None = None,
    *,
    service_token: str | None = None,
) -> FastAPI:
    """Create the narrow authority app; legacy Data CRUD routes are not mounted."""

    token = _required_service_token(service_token)
    store = DataStore.open(settings or load_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.init_schema()
        try:
            yield
        finally:
            await store.close()

    app = FastAPI(
        title="Eidolon Companion Authority",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/health", tags=["operations"])
    async def health() -> dict[str, str]:
        return {"status": "ready"}

    @app.get(
        "/api/companion-authority/v1/companions/{companion_id}",
        response_model=CompanionIdentityResponse,
        tags=["companion-authority"],
    )
    async def get_companion_identity(
        companion_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionIdentityResponse:
        _authorize(authorization, token)
        row = await store.companions.get(companion_id)
        if row is None:
            raise HTTPException(status_code=404, detail="companion not found")
        return CompanionIdentityResponse(
            companion_id=row.companion_id,
            owner_id=row.owner_id,
            lifecycle_state="active" if row.status == "active" else "inactive",
        )

    return app
