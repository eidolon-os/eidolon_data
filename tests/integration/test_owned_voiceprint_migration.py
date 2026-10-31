"""Migration protects owned evidence and enforces ownership below the API."""

import sqlite3
from contextlib import closing

import pytest
from alembic import command
from alembic.config import Config


def test_owned_voiceprint_migration_retains_evidence_and_removes_legacy(tmp_path, monkeypatch):
    path = tmp_path / "owned.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    config = Config("alembic.ini")
    command.upgrade(config, "0010_person_voiceprints")
    pcm = b"\x01\x00" * 24000
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("INSERT INTO owners(owner_id) VALUES ('o'), ('other')")
        db.execute("INSERT INTO persons VALUES ('person','o','Alice','active',1,'{}')")
        for profile, person in (("owned", "person"), ("legacy", None)):
            db.execute(
                "INSERT INTO speaker_profiles VALUES (?,?,?,?,?,?,?)",
                (profile, "o", "Alice", 7, profile, '{"template":1}', person),
            )
            db.execute("INSERT INTO speaker_recordings VALUES (?,?)", (profile, pcm))
            db.execute(
                "INSERT INTO voice_enrollments VALUES (?,?,?,?,?,?,?,?)",
                (profile, "o", profile, "{}", '{"evidence":1}', "ready", 0, person),
            )
    command.upgrade(config, "0011_owned_voiceprints")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        assert db.execute(
            "SELECT profile_id,person_id,revision,template_json FROM speaker_profiles"
        ).fetchall() == [("owned", "person", 7, '{"template":1}')]
        assert db.execute("SELECT profile_id,pcm FROM speaker_recordings").fetchall() == [
            ("owned", pcm)
        ]
        assert db.execute(
            "SELECT enrollment_id,person_id,samples_json FROM voice_enrollments"
        ).fetchall() == [("owned", "person", '{"evidence":1}')]
        for table in ("speaker_profiles", "voice_enrollments"):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f"UPDATE {table} SET person_id=NULL")
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f"UPDATE {table} SET owner_id='other'")
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        db.execute("DELETE FROM persons WHERE person_id='person'")
        for table in ("speaker_profiles", "speaker_recordings", "voice_enrollments"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)


@pytest.mark.parametrize(
    "start_revision,profiles",
    [("0012_person_memory_realms", ("a", "b")), ("0013_person_biometrics", ("a",))],
)
def test_single_voice_upgrade_retires_old_evidence(tmp_path, monkeypatch, start_revision, profiles):
    path = tmp_path / "duplicate.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    config = Config("alembic.ini")
    command.upgrade(config, start_revision)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("INSERT INTO owners(owner_id) VALUES ('o')")
        db.execute("INSERT INTO persons VALUES ('person','o','Alice','active',1,'{}')")
        for profile in profiles:
            db.execute(
                "INSERT INTO speaker_profiles(profile_id,owner_id,display_name,revision,enrollment_id,template_json,person_id) VALUES (?,?,?,?,?,?,?)",
                (profile, "o", "Alice", 1, profile, "{}", "person"),
            )
            db.execute("INSERT INTO speaker_recordings VALUES (?,?)", (profile, b"original"))
    command.upgrade(config, "head")
    with closing(sqlite3.connect(path)) as db:
        for table in ("speaker_profiles", "speaker_recordings", "voice_enrollments"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM persons").fetchone()[0] == 1
        assert db.execute("SELECT deleted FROM persons").fetchone()[0] == 0
        assert (
            db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "0017_person_lifecycle"
        )


def _insert(db, table, values):
    """Insert a row, giving every required column without a default a placeholder."""
    row = dict(values)
    for _, name, kind, notnull, default, primary in db.execute(f"PRAGMA table_info({table})"):
        if name not in row and notnull and default is None:
            row[name] = 0 if any(k in kind.upper() for k in ("INT", "BOOL", "FLOAT", "REAL")) else "x"
    db.execute(f"INSERT INTO {table}({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))


def test_registration_templates_carry_their_whole_registration_speech(tmp_path, monkeypatch):
    import json
    path = tmp_path / "speech.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    config = Config("alembic.ini")
    command.upgrade(config, "0027_owner_voices")

    def sample(ms):
        return {"model_id": "m", "weights_hash": "a" * 64, "preprocessing_version": "v",
                "vector": [1.0, 0.0], "speech_ms": ms, "quality": {}}

    with closing(sqlite3.connect(path)) as db, db:
        _insert(db, "owners", {"owner_id": "o"})
        for person, profile, registration in (("p", "registered", (6320, 5490, 6080)),
                                              ("q", "long", (12000, 12000, 12000))):
            _insert(db, "persons", {"person_id": person, "owner_id": "o", "display_name": person,
                                    "status": "active", "revision": 1, "profile_json": "{}"})
            _insert(db, "speaker_profiles", {
                "profile_id": profile, "owner_id": "o", "person_id": person, "display_name": person,
                "revision": 1, "enrollment_id": profile, "origin": "enrollment",
                "template_json": json.dumps(sample(registration[0]))})
            _insert(db, "voice_enrollments", {
                "enrollment_id": profile, "owner_id": "o", "profile_id": profile, "person_id": person,
                "state": "ready", "expires_at": 0, "request_json": "{}",
                "samples_json": json.dumps({"registration": {str(i): sample(ms) for i, ms in
                                                             enumerate(registration)}})})
    command.upgrade(config, "0028_registration_template_speech")
    with closing(sqlite3.connect(path)) as db:
        speech = {p: json.loads(t)["speech_ms"] for p, t in db.execute(
            "SELECT profile_id, template_json FROM speaker_profiles")}
    assert speech == {"registered": 6320 + 5490 + 6080, "long": 30000}
