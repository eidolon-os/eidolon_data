"""Upgrade populated Persons without changing their identity or retained Realm."""

import asyncio
import sqlite3
from contextlib import closing

from alembic import command
from alembic.config import Config


async def test_upgrade_retains_person_and_private_realm(tmp_path, monkeypatch):
    path = tmp_path / "maturity.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    config = Config("alembic.ini")
    await asyncio.to_thread(command.upgrade, config, "0017_person_lifecycle")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO owners(owner_id) VALUES ('owner')")
        db.execute(
            "INSERT INTO persons(person_id,owner_id,display_name,status,revision,profile_json) "
            "VALUES ('person','owner','甲','active',7,'{}')"
        )
        db.execute(
            "INSERT INTO memory_realms(realm_id,owner_id,person_id,engine,status,engine_config_json,policy_json) "
            "VALUES ('private','owner','person','mempalace','inactive','{}','{}')"
        )
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.upgrade, config, "head")
    with closing(sqlite3.connect(path)) as db:
        assert db.execute(
            "SELECT person_id,revision,maturity,is_named,status FROM persons"
        ).fetchall() == [("person", 7, "established", 1, "active")]
        assert db.execute("SELECT realm_id,person_id,status FROM memory_realms").fetchall() == [
            ("private", "person", "inactive")
        ]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
