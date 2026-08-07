"""Versioned, least-privilege Companion identity authority service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from eidolon_data import DataSettings, DataStore, load_settings

from .service_auth import authorize_service, required_service_token


class CompanionIdentityResponse(BaseModel):
    """Stable identity subset consumed by OS control-plane services."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["companion.identity"] = "companion.identity"
    companion_id: str = Field(min_length=1, max_length=64)
    owner_id: str = Field(min_length=1, max_length=64)
    lifecycle_state: Literal["active", "inactive"]


def create_app(
    settings: DataSettings | None = None,
    *,
    service_token: str | None = None,
) -> FastAPI:
    """Create the narrow authority app; legacy Data CRUD routes are not mounted."""

    token = required_service_token(
        service_token,
        environment_name="EIDOLON_DATA_COMPANION_AUTHORITY_TOKEN",
    )
    store = DataStore.open(settings or load_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.validate_schema()
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
        authorize_service(authorization, token)
        row = await store.companions.get(companion_id)
        if row is None:
            raise HTTPException(status_code=404, detail="companion not found")
        return CompanionIdentityResponse(
            companion_id=row.companion_id,
            owner_id=row.owner_id,
            lifecycle_state="active" if row.status == "active" else "inactive",
        )

    return app
