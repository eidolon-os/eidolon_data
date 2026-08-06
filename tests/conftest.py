from __future__ import annotations

import pytest

from eidolon_data import DataSettings, DataStore


@pytest.fixture
async def store(tmp_path):
    value = DataStore.open(
        DataSettings(
            sqlite_path=str(tmp_path / "system.sqlite3"),
            object_store_path=str(tmp_path / "objects"),
        )
    )
    await value.init_schema()
    try:
        yield value
    finally:
        await value.close()
