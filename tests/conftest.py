"""Shared test fixtures for eidolon_data.

The ``store`` fixture (tmp-file sqlite + ``init_schema`` + a fake memory engine)
was duplicated across test modules; it lives here once now.
"""

from __future__ import annotations

import pytest

from eidolon_data import DataSettings, DataStore
from eidolon_data.schema.types import (
    MemoryEngineHealth,
    MemoryIngestResult,
    MemoryItem,
    RecallResult,
)


class FakeMemoryEngine:
    async def ingest(self, item: MemoryItem) -> MemoryIngestResult:
        return MemoryIngestResult(
            memory_id=item.memory_id,
            realm_id=item.realm_id,
            engine="fake",
            external_key=f"fake://{item.realm_id}/{item.memory_id}",
            metadata={"source": "test"},
        )

    async def recall(self, realm_id: str, query: str, options) -> RecallResult:
        return RecallResult(metadata={"realm_id": realm_id, "query": query, "top_k": options.top_k})

    async def delete(self, memory_id: str) -> None:
        return None

    async def health(self) -> MemoryEngineHealth:
        return MemoryEngineHealth(ok=True, engine="fake")


@pytest.fixture
async def store(tmp_path):
    data_store = DataStore.open(
        DataSettings(sqlite_path=str(tmp_path / "eidolon_data.sqlite3")),
        memory_engine=FakeMemoryEngine(),
    )
    await data_store.init_schema()
    try:
        yield data_store
    finally:
        await data_store.close()
