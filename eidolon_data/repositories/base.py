"""Shared repository base."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker


class Repository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

