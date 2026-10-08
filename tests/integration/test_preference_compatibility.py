"""Stored compatibility is explicit; new writes remain strict."""

import json
import sqlite3
from contextlib import closing

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError

from eidolon_data.schema import CompanionRow


def _database(tmp_path, monkeypatch, configs):
    path = tmp_path / "preferences.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    command.upgrade(Config("alembic.ini"), "0004_smarthome_v2_columns")
    with closing(sqlite3.connect(path)) as db:
        db.execute("INSERT INTO owners(owner_id) VALUES ('o')")
        for index, config in enumerate(configs):
            db.execute(
                "INSERT INTO companions(companion_id,owner_id,runtime_config_json) VALUES (?, 'o', ?)",
                (str(index), json.dumps(config)),
            )
        db.commit()
    return path


def _configs(path):
    with closing(sqlite3.connect(path)) as db:
        return [
            json.loads(row[0])
            for row in db.execute(
                "SELECT runtime_config_json FROM companions ORDER BY companion_id"
            )
        ]


def test_known_duplicate_is_migrated_without_losing_voice_or_other_config(tmp_path, monkeypatch):
    voice = {"profile_id": "warm-female", "bindings": [{"voice_id": "longwan_v3"}]}
    old = {
        "conversation_preferences": {"response_length": "brief", "voice_profile_id": "warm-female"},
        "companion_voice": voice,
        "preference_revision": 1,
        "other_namespace": {"x": 1},
    }
    path = _database(tmp_path, monkeypatch, [old, {}])
    command.upgrade(Config("alembic.ini"), "head")
    expected = {**old, "conversation_preferences": {"response_length": "brief"}}
    assert _configs(path) == [expected, {}]
    command.downgrade(Config("alembic.ini"), "0004_smarthome_v2_columns")
    command.upgrade(Config("alembic.ini"), "head")
    assert _configs(path) == [expected, {}]


@pytest.mark.parametrize(
    "bad",
    [
        {"conversation_preferences": {"voice_profile_id": "warm-female"}},
        {
            "conversation_preferences": {"voice_profile_id": "warm-female"},
            "companion_voice": {"profile_id": "other"},
        },
        {"conversation_preferences": {"unknown": "meaningful"}},
        {"conversation_preferences": {"response_length": "invalid"}},
    ],
)
def test_ambiguous_data_aborts_before_any_row_is_changed(tmp_path, monkeypatch, bad):
    good = {
        "conversation_preferences": {"voice_profile_id": "warm-female"},
        "companion_voice": {"profile_id": "warm-female"},
    }
    path = _database(tmp_path, monkeypatch, [good, bad])
    with pytest.raises(ValueError):
        command.upgrade(Config("alembic.ini"), "head")
    assert _configs(path) == [good, bad]
    with closing(sqlite3.connect(path)) as db:
        assert (
            db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "0004_smarthome_v2_columns"
        )


def test_new_raw_writes_cannot_reintroduce_retired_or_unknown_preferences():
    row = CompanionRow(
        runtime_config_json={"conversation_preferences": {"advice": "proactive"}, "other": 1}
    )
    assert row.runtime_config_json["other"] == 1
    for preferences in ({"voice_profile_id": "warm-female"}, {"unknown": 1}, {"advice": "never"}):
        with pytest.raises(ValidationError):
            row.runtime_config_json = {"conversation_preferences": preferences}
        with pytest.raises(ValidationError):
            CompanionRow(runtime_config_json={"conversation_preferences": preferences})
