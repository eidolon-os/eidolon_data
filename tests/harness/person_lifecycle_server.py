"""Disposable Data authority for cross-process management contract tests."""

import argparse
import asyncio
from pathlib import Path

import uvicorn

from eidolon_data import DataSettings, DataStore
from eidolon_data.api.companion_authority import create_app

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--state", type=Path, required=True)
parser.add_argument("--token", required=True)
args = parser.parse_args()
settings = DataSettings(
    sqlite_path=str(args.state / "data.sqlite3"), object_store_path=str(args.state / "objects")
)


async def prepare():
    store = DataStore.open(settings)
    await store.init_schema()
    await store.owner_commands.create_owner(owner_id="owner-1")
    await store.owner_commands.create_owner(owner_id="foreign-owner")
    await store.close()


asyncio.run(prepare())
app = create_app(settings, service_token=args.token, memory_roster_token=args.token)
uvicorn.run(app, host="127.0.0.1", port=args.port)
