"""Command line entry points for Eidolon Data."""

from __future__ import annotations

import argparse
import asyncio

from eidolon_data import DataSettings, DataStore, load_settings


async def _delete_owner(args: argparse.Namespace) -> None:
    settings = DataSettings(sqlite_path=args.sqlite_path) if args.sqlite_path else load_settings()
    store = DataStore.open(settings)
    try:
        await store.validate_schema()
        result = await store.owner_deletion.delete_owner(args.owner_id)
    finally:
        await store.close()
    print(
        f"deleted={str(result.deleted).lower()} owner_id={result.owner_id} "
        f"deleted_rows={result.deleted_rows} realm_ids={list(result.realm_ids)} "
        f"object_storage_keys={list(result.object_storage_keys)}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Eidolon Data management CLI")
    subcommands = parser.add_subparsers(dest="command", required=True)

    delete_owner = subcommands.add_parser(
        "delete-owner", help="Delete one owner and its owned data"
    )
    delete_owner.add_argument("owner_id", nargs="?", default="owner-default")
    delete_owner.add_argument(
        "--sqlite-path",
        default=None,
        help="Override SQLite path. Defaults to ~/eidolon/data/eidolon-system.sqlite3.",
    )
    delete_owner.set_defaults(func=_delete_owner)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(args.func(args))
