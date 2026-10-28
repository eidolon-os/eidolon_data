import asyncio
import sqlite3
from contextlib import closing

import pytest
from alembic import command
from alembic.config import Config

from eidolon_data import DataSettings, DataStore
from eidolon_data.services.persons import PersonConflict


async def test_retained_private_realm_gets_only_an_inactive_archive(tmp_path, monkeypatch):
    path = tmp_path / "retained.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    config = Config("alembic.ini")
    await asyncio.to_thread(command.upgrade, config, "0015_person_archive")
    person = "a" * 32
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO owners(owner_id) VALUES ('owner')")
        db.execute(
            "INSERT INTO memory_realms(realm_id,owner_id,engine,status,engine_config_json,policy_json,person_id) VALUES ('private','owner','mempalace','active','{}','{}',?)",
            (person,),
        )
        db.execute(
            "INSERT INTO memory_realms(realm_id,owner_id,engine,status,engine_config_json,policy_json) VALUES ('default','owner','mempalace','active','{}','{}')"
        )
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.upgrade, config, "head")
    store = DataStore.open(
        DataSettings(sqlite_path=str(path), object_store_path=str(tmp_path / "objects"))
    )
    try:
        assert await store.persons.list("owner") == []
        archived = await store.persons.list("owner", deleted=True)
        assert len(archived) == 1 and archived[0].person_id == person
        assert archived[0].status == "inactive"
        assert archived[0].identity_state == "missing"
        with pytest.raises(PersonConflict, match="identity information is missing"):
            await store.persons.restore("owner", person, archived[0].revision)
        intent = await store.persons.permanent_delete("owner", person, archived[0].revision)
        assert intent["realm_id"] == "private" and intent["state"] == "pending"
        with closing(sqlite3.connect(path)) as db:
            assert (
                db.execute("SELECT status FROM memory_realms WHERE realm_id='default'").fetchone()[
                    0
                ]
                == "active"
            )
            assert db.execute("SELECT count(*) FROM speaker_profiles").fetchone()[0] == 0
        await store.persons.permanent_delete("owner", person, intent["revision"], complete=True)
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT count(*) FROM persons").fetchone()[0] == 0
            assert db.execute("SELECT realm_id FROM memory_realms").fetchall() == [("default",)]
            assert db.execute("SELECT state FROM person_deletions").fetchone()[0] == "completed"
    finally:
        await store.close()
