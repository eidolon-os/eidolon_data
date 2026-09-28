"""The smart-home registry tables, as a fresh Alembic upgrade creates them.

The service refuses these states first and says why; the schema is what still
refuses them if a future code path forgets to ask.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing

import pytest
from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.integration


@pytest.fixture
def migrated(tmp_path, monkeypatch) -> Iterator[sqlite3.Connection]:
    path = tmp_path / "smarthome.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    command.upgrade(Config("alembic.ini"), "head")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("INSERT INTO owners(owner_id) VALUES ('owner-a'), ('owner-b')")
        connection.execute(
            "INSERT INTO smarthome_areas(owner_id, area_id, name, position) "
            "VALUES ('owner-a', 'living', '客厅', 0), ('owner-b', 'study', '书房', 0)"
        )
        connection.execute(
            "INSERT INTO smarthome_devices(owner_id, device_id, name, device_type, area_id, position) "
            "VALUES ('owner-a', 'lamp', '灯', 'light', 'living', 0)"
        )
        connection.execute(
            "INSERT INTO smarthome_scenes(owner_id, scene_id, name, position) "
            "VALUES ('owner-a', 'home', '回家', 0)"
        )
        connection.execute(
            "INSERT INTO smarthome_scene_actions(owner_id, scene_id, position, device_id, trait, command) "
            "VALUES ('owner-a', 'home', 0, 'lamp', 'on_off', 'on')"
        )
        connection.execute(
            "INSERT INTO smarthome_placements(owner_id, device_ref, area_id) "
            "VALUES ('owner-a', 'korvo-1', 'living')"
        )
        connection.execute(
            "INSERT INTO smarthome_registries(owner_id, revision) VALUES ('owner-a', 5)"
        )
        connection.commit()
        yield connection


def test_a_row_cannot_reference_another_owners_row(migrated) -> None:
    for statement in (
        # owner-a's device in owner-b's area
        "INSERT INTO smarthome_devices(owner_id, device_id, name, device_type, area_id, position) "
        "VALUES ('owner-a', 'spy', '灯', 'light', 'study', 1)",
        # owner-b's panel in owner-a's area
        "INSERT INTO smarthome_placements(owner_id, device_ref, area_id) "
        "VALUES ('owner-b', 'box-3', 'living')",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            migrated.execute(statement)
    # owner-b's scene driving owner-a's device
    migrated.execute(
        "INSERT INTO smarthome_scenes(owner_id, scene_id, name, position) "
        "VALUES ('owner-b', 'borrow', '借', 0)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated.execute(
            "INSERT INTO smarthome_scene_actions(owner_id, scene_id, position, device_id, trait, command) "
            "VALUES ('owner-b', 'borrow', 0, 'lamp', 'on_off', 'on')"
        )


def test_in_use_areas_and_devices_do_not_cascade_away(migrated) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        migrated.execute("DELETE FROM smarthome_devices WHERE device_id = 'lamp'")
    migrated.execute("DELETE FROM smarthome_scene_actions")
    migrated.execute("DELETE FROM smarthome_devices WHERE device_id = 'lamp'")
    # The panel still stands in the living room.
    with pytest.raises(sqlite3.IntegrityError):
        migrated.execute("DELETE FROM smarthome_areas WHERE area_id = 'living'")
    migrated.execute("DELETE FROM smarthome_placements")
    migrated.execute("DELETE FROM smarthome_areas WHERE area_id = 'living'")


def test_a_scene_takes_its_own_actions_and_an_owner_takes_everything(migrated) -> None:
    migrated.execute("DELETE FROM smarthome_scenes WHERE scene_id = 'home'")
    assert migrated.execute("SELECT count(*) FROM smarthome_scene_actions").fetchone() == (0,)

    migrated.execute("DELETE FROM owners WHERE owner_id = 'owner-a'")
    for table in (
        "smarthome_registries",
        "smarthome_areas",
        "smarthome_devices",
        "smarthome_scenes",
        "smarthome_scene_actions",
        "smarthome_placements",
    ):
        assert migrated.execute(
            f"SELECT count(*) FROM {table} WHERE owner_id = 'owner-a'"
        ).fetchone() == (0,), table
    assert migrated.execute("SELECT count(*) FROM smarthome_areas").fetchone() == (1,)


def test_revision_and_area_names_are_constrained(migrated) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        migrated.execute(
            "INSERT INTO smarthome_registries(owner_id, revision) VALUES ('owner-b', 0)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        migrated.execute(
            "INSERT INTO smarthome_areas(owner_id, area_id, name, position) "
            "VALUES ('owner-a', 'lounge', '客厅', 1)"
        )
    # Names are unique per Owner, not across Owners.
    migrated.execute(
        "INSERT INTO smarthome_areas(owner_id, area_id, name, position) "
        "VALUES ('owner-b', 'living', '客厅', 1)"
    )
