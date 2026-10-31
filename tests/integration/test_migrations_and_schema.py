from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest
from alembic import command
from alembic.config import Config

import eidolon_data.schema  # noqa: F401
from eidolon_data import DataSettings, DataStore
from eidolon_data.db.base import Base

pytestmark = pytest.mark.integration

EXPECTED_TABLES = set(Base.metadata.tables)


def test_fresh_alembic_upgrade_matches_current_model_exactly(tmp_path, monkeypatch) -> None:
    path = tmp_path / "migrated.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    command.upgrade(Config("alembic.ini"), "head")

    with closing(sqlite3.connect(path)) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == EXPECTED_TABLES | {"alembic_version"}
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0029_channel_calibration_samples",
        )
        for table_name, table in Base.metadata.tables.items():
            actual_columns = {
                row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")')
            }
            assert actual_columns == set(table.columns.keys()), table_name
    command.check(Config("alembic.ini"))


def test_retired_admission_is_not_restored_by_downgrade(tmp_path, monkeypatch):
    path = tmp_path / "migration-cycle.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    with pytest.raises(RuntimeError, match="Saved teams must not be discarded"):
        command.downgrade(config, "0009_persons")


def test_voice_release_migration_preserves_policy_and_refuses_unsafe_downgrade(
    tmp_path, monkeypatch
):
    path = tmp_path / "voice-release.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    config = Config("alembic.ini")
    command.upgrade(config, "0007_voice_recordings")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("INSERT INTO owners(owner_id) VALUES ('o')")
        connection.execute("INSERT INTO companions(companion_id, owner_id) VALUES ('c', 'o')")
        connection.execute(
            "INSERT INTO companion_voice_policies(companion_id, mode, revision) "
            "VALUES ('c', 'allowlist', 4)"
        )
        connection.commit()
    command.upgrade(config, "0008_voice_admission")
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute(
            "SELECT mode, revision, approval_json FROM companion_voice_policies"
        ).fetchone() == ("allowlist", 4, None)
    with pytest.raises(RuntimeError, match="disable voice admission"):
        command.downgrade(config, "0007_voice_recordings")
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0008_voice_admission",
        )
        connection.execute("UPDATE companion_voice_policies SET mode = 'off'")
        connection.commit()
    command.downgrade(config, "0007_voice_recordings")


async def test_migrated_database_passes_fail_closed_schema_validation(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "validated.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    # Alembic's env owns an event loop, so run the synchronous command in a thread.
    import asyncio

    await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")
    store = DataStore.open(DataSettings(sqlite_path=str(path), sqlite_read_only=True))
    try:
        await store.validate_schema()
    finally:
        await store.close()


async def test_schema_validation_rejects_retired_or_unknown_tables(tmp_path) -> None:
    path = tmp_path / "drift.sqlite3"
    store = DataStore.open(DataSettings(sqlite_path=str(path)))
    await store.init_schema()
    await store.close()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE devices (device_id TEXT PRIMARY KEY)")
        connection.commit()

    drifted = DataStore.open(DataSettings(sqlite_path=str(path)))
    try:
        with pytest.raises(RuntimeError, match="devices"):
            await drifted.validate_schema()
    finally:
        await drifted.close()


def test_database_constraints_enforce_the_three_identity_axes(tmp_path, monkeypatch) -> None:
    """kind, lifecycle_state and the Owner's default pointer are independent.

    They used to be one column. ``role='primary'`` meant both "this is the
    default" and "this is a plain conversational companion", so a guard could
    not be a default and changing the default rewrote a type. The schema now
    refuses values in each axis on its own, and "which one is the default" is a
    pointer on the Owner rather than a flag needing a cross-row uniqueness rule.
    """
    path = tmp_path / "constraints.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    command.upgrade(Config("alembic.ini"), "head")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO owners(owner_id, display_name) VALUES ('owner-a', 'A'), ('owner-b', 'B')"
        )
        for column, bad in (("kind", "primary"), ("lifecycle_state", "inactive")):
            # 'primary' and 'inactive' are the two values the old column
            # carried that no longer mean anything: one was a routing fact, the
            # other conflated "cannot run" with "the Owner archived it".
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    f"INSERT INTO companions(companion_id, owner_id, {column}) "
                    f"VALUES ('bad-{column}', 'owner-a', '{bad}')"
                )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO companions(companion_id, owner_id, revision) "
                "VALUES ('bad-revision', 'owner-a', 0)"
            )
        connection.execute(
            "INSERT INTO companions(companion_id, owner_id, kind) "
            "VALUES ('companion-a', 'owner-a', 'guard')"
        )
        # A guard is a kind, so storing one is fine; whether it may be a default
        # is a rule above this layer, not a column value.
        connection.execute(
            "UPDATE owners SET default_companion_id = 'companion-a' WHERE owner_id = 'owner-a'"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE owners SET default_companion_id = 'no-such-companion' "
                "WHERE owner_id = 'owner-b'"
            )
        # A realm belongs to an Owner, so there is no cross-owner companion
        # pairing left to forbid. What the schema forbids instead is a second
        # active realm for one Owner — the state nothing downstream can resolve,
        # because routing would have to pick between two halves of one memory.
        connection.execute(
            "INSERT INTO memory_realms(realm_id, owner_id) VALUES ('realm-a', 'owner-a')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO memory_realms(realm_id, owner_id) VALUES ('realm-a-second', 'owner-a')"
            )
        # Retiring the first one frees the Owner to have another.
        connection.execute(
            "UPDATE memory_realms SET status = 'inactive' WHERE realm_id = 'realm-a'"
        )
        connection.execute(
            "INSERT INTO memory_realms(realm_id, owner_id) VALUES ('realm-a-second', 'owner-a')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO memory_realms(realm_id, owner_id) "
                "VALUES ('realm-no-owner', 'owner-missing')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO companion_face_assets("
                "face_asset_id, companion_id, owner_id, version, cond_storage_key, "
                "cond_content_type, cond_size_bytes, cond_sha256"
                ") VALUES ("
                "'face-cross-owner', 'companion-a', 'owner-b', 1, 'cross-owner.jpg', "
                "'image/jpeg', 1, "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'"
                ")"
            )


def test_artwork_upgrade_uses_initial_preset_and_preserves_explicit_choices(tmp_path, monkeypatch):
    import json

    path = tmp_path / "artwork.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    config = Config("alembic.ini")
    command.upgrade(config, "0001_system_data_v2")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("INSERT INTO owners(owner_id) VALUES ('o')")
        for identity, profile, preset, rev in [
            ("water", {"other": 1}, "water", "1"),
            ("custom", {}, None, None),
            ("future", {}, "fire", "9"),
            ("chosen", {"artwork_id": "my-choice"}, "earth", "1"),
        ]:
            connection.execute(
                "INSERT INTO companions(companion_id,owner_id,profile_json) VALUES (?, 'o', ?)",
                (identity, json.dumps(profile)),
            )
            genome = {"provenance": {"source_preset_id": preset, "source_preset_revision": rev}}
            connection.execute(
                "INSERT INTO persona_genomes(genome_id,companion_id,version,schema_version,genome_hash,realizer_version,genome_json) VALUES (?,?,1,'eidolon.persona_genome','hash','eidolon.persona_realizer',?)",
                (identity, identity, json.dumps(genome)),
            )
        connection.commit()
    command.upgrade(config, "0009_persons")
    command.downgrade(config, "0001_system_data_v2")
    command.upgrade(config, "0009_persons")
    with closing(sqlite3.connect(path)) as connection:
        profiles = {
            identity: json.loads(raw)
            for identity, raw in connection.execute(
                "SELECT companion_id,profile_json FROM companions"
            )
        }
    assert profiles == {
        "water": {"other": 1, "artwork_id": "five-elements/1/water"},
        "custom": {},
        "future": {},
        "chosen": {"artwork_id": "my-choice"},
    }


def test_person_migration_preserves_unassigned_voiceprints_and_blocks_data_loss(
    tmp_path, monkeypatch
):
    path = tmp_path / "people.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    config = Config("alembic.ini")
    command.upgrade(config, "0008_voice_admission")
    with closing(sqlite3.connect(path)) as db:
        db.execute("INSERT INTO owners(owner_id) VALUES ('o')")
        db.execute("INSERT INTO speaker_profiles VALUES ('p', 'o', '甲', 3, 'e', '{}')")
        db.commit()
    command.upgrade(config, "0009_persons")
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT profile_id, revision FROM speaker_profiles").fetchall() == [
            ("p", 3)
        ]
        assert db.execute("SELECT count(*) FROM persons").fetchone() == (0,)
        db.execute("INSERT INTO persons VALUES ('person', 'o', '甲', 'active', 1, '{}')")
        db.commit()
    with pytest.raises(RuntimeError, match="person downgrade requires"):
        command.downgrade(config, "0008_voice_admission")
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT person_id FROM persons").fetchall() == [("person",)]


def test_voiceprint_migration_keeps_recording_evidence_without_companion(tmp_path, monkeypatch):
    path = tmp_path / "voice-to-person.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    config = Config("alembic.ini")
    command.upgrade(config, "0009_persons")
    pcm = b"\x01\x00" * 24000
    with closing(sqlite3.connect(path)) as db:
        db.execute("INSERT INTO owners(owner_id) VALUES ('o')")
        db.execute("INSERT INTO companions(companion_id,owner_id) VALUES ('c','o')")
        db.execute("INSERT INTO speaker_profiles VALUES ('p','o','甲',7,'e','{}')")
        db.execute("INSERT INTO speaker_recordings VALUES ('p',?)", (pcm,))
        db.execute(
            "INSERT INTO voice_enrollments VALUES ('e','o','c','p','{}','{\"evaluation\":{\"v\":1}}','ready',0)"
        )
        db.execute(
            "INSERT INTO voice_enrollments VALUES ('pending','o','c','new','{}','{\"sample\":1}','collecting',0)"
        )
        db.execute("INSERT INTO companion_voice_policies VALUES ('c','allowlist',9,'{}')")
        db.execute("INSERT INTO companion_speaker_bindings VALUES ('c','p')")
        db.commit()
    command.upgrade(config, "0010_person_voiceprints")
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("DELETE FROM companions WHERE companion_id='c'")
        db.commit()
        assert db.execute(
            "SELECT profile_id,revision,person_id FROM speaker_profiles"
        ).fetchall() == [("p", 7, None)]
        assert db.execute("SELECT pcm FROM speaker_recordings").fetchone() == (pcm,)
        assert db.execute(
            "SELECT samples_json FROM voice_enrollments WHERE enrollment_id='e'"
        ).fetchone() == ('{"evaluation":{"v":1}}',)
        assert db.execute(
            "SELECT state,samples_json FROM voice_enrollments WHERE enrollment_id='pending'"
        ).fetchone() == ("cancelled", "{}")
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name IN ('companion_voice_policies','companion_speaker_bindings')"
        ).fetchall()
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_person_realm_migration_preserves_default_and_guards_downgrade(tmp_path, monkeypatch):
    path = tmp_path / "person-realm.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    config = Config("alembic.ini")
    command.upgrade(config, "0011_owned_voiceprints")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO owners(owner_id) VALUES ('o')")
        db.execute(
            "INSERT INTO memory_realms(realm_id,owner_id,status) VALUES ('default','o','active')"
        )
    command.upgrade(config, "head")
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute(
            "SELECT person_id FROM memory_realms WHERE realm_id='default'"
        ).fetchone() == (None,)
        db.execute(
            "INSERT INTO memory_realms(realm_id,owner_id,status,person_id) VALUES ('a','o','active','a')"
        )
        db.execute(
            "INSERT INTO memory_realms(realm_id,owner_id,status,person_id) VALUES ('b','o','active','b')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO memory_realms(realm_id,owner_id,status) VALUES ('second','o','active')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO memory_realms(realm_id,owner_id,status,person_id) VALUES ('duplicate','o','active','a')"
            )
    with pytest.raises(RuntimeError, match="Saved teams must not be discarded"):
        command.downgrade(config, "0011_owned_voiceprints")


def test_channel_calibration_migration_holds_only_public_replay_and_downgrades(
    tmp_path, monkeypatch
):
    path = tmp_path / "channel-calibration.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    config = Config("alembic.ini")
    command.upgrade(config, "0028_registration_template_speech")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO owners(owner_id) VALUES ('o')")
    command.upgrade(config, "head")
    insert = (
        "INSERT INTO channel_calibration_samples(sample_id,owner_id,channel,source,batch_id,"
        "captured_at,sample_json,created_at) VALUES (?,'o','mobile',?,'replay-1',1.0,'{}',"
        "'2026-11-01 00:00:00')"
    )
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(insert, ("cc_1", "public_replay"))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(insert, ("cc_2", "household"))
        db.execute("DELETE FROM owners WHERE owner_id='o'")
        assert db.execute("SELECT count(*) FROM channel_calibration_samples").fetchone() == (0,)
    command.downgrade(config, "0028_registration_template_speech")
    with closing(sqlite3.connect(path)) as db:
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name='channel_calibration_samples'"
        ).fetchall()
