"""Owner-scoped saved team revisions; no media or runtime state."""

from typing import Any
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from eidolon_data.db.base import Base


class TeamRow(Base):
    __tablename__ = "teams"
    owner_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    host_id: Mapped[str] = mapped_column(String(128))
    active_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deleted: Mapped[bool] = mapped_column(default=False)
    revision: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        ForeignKeyConstraint(["owner_id"], ["owners.owner_id"]),
        CheckConstraint("revision > 0", name="team_revision_positive"),
    )


class TeamRevisionRow(Base):
    __tablename__ = "team_revisions"
    owner_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    configuration: Mapped[dict[str, Any]] = mapped_column()
    __table_args__ = (
        ForeignKeyConstraint(["owner_id", "team_id"], ["teams.owner_id", "teams.team_id"]),
        CheckConstraint("revision > 0", name="team_saved_revision_positive"),
    )


class TeamStartRow(Base):
    """Durable start identity, not a claim that media is running."""

    __tablename__ = "team_starts"
    owner_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(Integer)
    closed: Mapped[bool] = mapped_column(default=False)
    selection: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    terminal_status: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "team_id", "revision"],
            ["team_revisions.owner_id", "team_revisions.team_id", "team_revisions.revision"],
        ),
    )
