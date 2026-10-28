import asyncio
import sqlite3
from contextlib import closing

from alembic import command
from alembic.config import Config


async def test_origin_migration_preserves_enrollment_template_and_recording(tmp_path, monkeypatch):
    path = tmp_path / 'origin.sqlite3'
    monkeypatch.setenv('EIDOLON_DATA_DATABASE_URL', f'sqlite+aiosqlite:///{path}')
    config = Config('alembic.ini')
    await asyncio.to_thread(command.upgrade, config, '0021_candidate_activity')
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO owners(owner_id) VALUES ('owner')")
        db.execute("INSERT INTO persons(person_id,owner_id,display_name,status,revision,profile_json) VALUES ('p','owner','A','active',3,'{}')")
        db.execute("INSERT INTO speaker_profiles(profile_id,owner_id,person_id,display_name,revision,enrollment_id,template_json) VALUES ('v','owner','p','A',4,'e','{}')")
        db.execute("INSERT INTO speaker_recordings(profile_id,pcm) VALUES ('v',?)", (b'original-pcm',))
    await asyncio.to_thread(command.upgrade, config, 'head')
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT origin,enrollment_id,source_observation_id,revision FROM speaker_profiles').fetchone() == ('enrollment', 'e', None, 4)
        assert db.execute('SELECT pcm FROM speaker_recordings').fetchone() == (b'original-pcm',)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
