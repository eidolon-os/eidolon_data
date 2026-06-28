"""Command line entry points for Eidolon Data."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from eidolon_data import DataSettings, DataStore, load_settings
from eidolon_data.services.id_migration import normalize_sqlite_ids


async def _init_db(args: argparse.Namespace) -> None:
    settings = DataSettings(sqlite_path=args.sqlite_path) if args.sqlite_path else load_settings()
    store = DataStore.open(settings)
    try:
        await store.init_schema()
    finally:
        await store.close()
    print(str(Path(settings.sqlite_path).expanduser()))


async def _delete_owner(args: argparse.Namespace) -> None:
    settings = DataSettings(sqlite_path=args.sqlite_path) if args.sqlite_path else load_settings()
    store = DataStore.open(settings)
    try:
        await store.init_schema()
        result = await store.maintenance.delete_owner_tree(args.owner_id)
    finally:
        await store.close()
    print(
        f"deleted={str(result.deleted).lower()} owner_id={result.owner_id} "
        f"devices={result.devices} companions={result.companions} "
        f"conversations={result.conversations} jobs={result.jobs} events={result.events}"
    )


async def _normalize_ids(args: argparse.Namespace) -> None:
    settings = DataSettings(sqlite_path=args.sqlite_path) if args.sqlite_path else load_settings()
    result = normalize_sqlite_ids(settings.sqlite_path)
    print(
        f"database={result.database_path} mappings={result.mappings} rows_updated={result.rows_updated}"
    )


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

    delete_owner = subcommands.add_parser("delete-owner", help="Delete one owner and its owned data")
    delete_owner.add_argument("owner_id", nargs="?", default="owner-default")
    delete_owner.add_argument(
        "--sqlite-path",
        default=None,
        help="Override SQLite path. Defaults to ~/eidolon/data/eidolon.sqlite3.",
    )
    delete_owner.set_defaults(func=_delete_owner)

    normalize_ids = subcommands.add_parser(
        "normalize-ids",
        help="Normalize generated IDs from colon separators to underscores",
    )
    normalize_ids.add_argument(
        "--sqlite-path",
        default=None,
        help="Override SQLite path. Defaults to ~/eidolon/data/eidolon.sqlite3.",
    )
    normalize_ids.set_defaults(func=_normalize_ids)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(args.func(args))
