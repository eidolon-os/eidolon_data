"""Migration protects owned evidence and enforces ownership below the API."""
from contextlib import closing
import sqlite3

from alembic import command
from alembic.config import Config
import pytest


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
            db.execute("INSERT INTO speaker_profiles VALUES (?,?,?,?,?,?,?)",
                       (profile, 'o', 'Alice', 7, profile, '{"template":1}', person))
            db.execute("INSERT INTO speaker_recordings VALUES (?,?)", (profile, pcm))
            db.execute("INSERT INTO voice_enrollments VALUES (?,?,?,?,?,?,?,?)",
                       (profile, 'o', profile, '{}', '{"evidence":1}', 'ready', 0, person))
    command.upgrade(config, "head")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        assert db.execute("SELECT profile_id,person_id,revision,template_json FROM speaker_profiles").fetchall() == [
            ('owned', 'person', 7, '{"template":1}')]
        assert db.execute("SELECT profile_id,pcm FROM speaker_recordings").fetchall() == [('owned', pcm)]
        assert db.execute("SELECT enrollment_id,person_id,samples_json FROM voice_enrollments").fetchall() == [
            ('owned', 'person', '{"evidence":1}')]
        for table in ('speaker_profiles', 'voice_enrollments'):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f"UPDATE {table} SET person_id=NULL")
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f"UPDATE {table} SET owner_id='other'")
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        db.execute("DELETE FROM persons WHERE person_id='person'")
        for table in ('speaker_profiles', 'speaker_recordings', 'voice_enrollments'):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
