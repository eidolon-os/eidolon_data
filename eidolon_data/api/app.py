"""Optional FastAPI app for cross-process Eidolon Data access."""

from __future__ import annotations

from fastapi import FastAPI

from eidolon_data import DataSettings, DataStore, load_settings
from eidolon_data.api.routers.companions import router as companions_router
from eidolon_data.api.routers.owners import router as owners_router


def create_app(settings: DataSettings | None = None) -> FastAPI:
    app = FastAPI(title="Eidolon Data", version="0.1.0")
    store = DataStore.open(settings or load_settings())
    app.state.store = store

    @app.on_event("startup")
    async def _startup() -> None:
        await store.init_schema()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await store.close()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(owners_router)
    app.include_router(companions_router)

    return app


app = create_app()
