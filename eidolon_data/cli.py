"""Command line entry points for Eidolon Data."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from eidolon_data import DataSettings, DataStore, load_settings


async def _init_db(args: argparse.Namespace) -> None:
    settings = DataSettings(sqlite_path=args.sqlite_path) if args.sqlite_path else load_settings()
    store = DataStore.open(settings)
    try:
        await store.init_schema()
    finally:
        await store.close()
    print(str(Path(settings.sqlite_path).expanduser()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Eidolon Data management CLI")
    subcommands = parser.add_subparsers(dest="command", required=True)

    init_db = subcommands.add_parser("init-db", help="Create the Eidolon Data SQLite schema")
    init_db.add_argument(
        "--sqlite-path",
        default=None,
        help="Override SQLite path. Defaults to ~/eidolon/data/eidolon.sqlite3.",
    )
    init_db.set_defaults(func=_init_db)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(args.func(args))
