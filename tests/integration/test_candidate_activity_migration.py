"""Historical candidates get an explicit retention baseline, not fake speech."""
import asyncio
import sqlite3
import time
from contextlib import closing

from alembic import command
from alembic.config import Config


async def test_migration_initializes_only_candidate_retention(tmp_path, monkeypatch):
    path = tmp_path / 'activity.sqlite3'
    monkeypatch.setenv('EIDOLON_DATA_DATABASE_URL', f'sqlite+aiosqlite:///{path}')
    config = Config('alembic.ini')
    await asyncio.to_thread(command.upgrade, config, '0020_candidate_voice_observations')
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO owners(owner_id) VALUES ('owner')")
        for person, maturity in [('candidate', 'candidate'), ('established', 'established')]:
            db.execute("INSERT INTO persons(person_id,owner_id,display_name,status,revision,profile_json,maturity) "
                       "VALUES (?,'owner',?,'active',7,'{}',?)", (person, person, maturity))
    before = int(time.time())
    await asyncio.to_thread(command.upgrade, config, 'head')
    await asyncio.to_thread(command.upgrade, config, 'head')
    with closing(sqlite3.connect(path)) as db:
        candidate = db.execute("SELECT revision,candidate_activity_at FROM persons WHERE person_id='candidate'").fetchone()
        assert candidate[0] == 7 and before <= candidate[1] <= time.time()
        assert db.execute("SELECT candidate_activity_at FROM persons WHERE person_id='established'").fetchone() == (None,)
        assert db.execute('SELECT count(*) FROM candidate_voice_observations').fetchone() == (0,)
