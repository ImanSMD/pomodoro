from __future__ import annotations

from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDelete, Timestamps, UUIDPrimaryKey

# Canonical vocabularies. schemas/session.py asserts its Literals match these,
# the same guard tasks uses, so the check constraints and the API cannot drift.
SESSION_KINDS: Final = ("work", "short_break", "long_break")
SESSION_STATUSES: Final = ("running", "completed", "cancelled")

_KINDS_SQL = ", ".join(f"'{k}'" for k in SESSION_KINDS)
_STATUSES_SQL = ", ".join(f"'{s}'" for s in SESSION_STATUSES)


class Session(UUIDPrimaryKey, Timestamps, SoftDelete, Base):
    """One timer run.

    The partial unique index in migration 0004 — not declared here because an
    index with a WHERE clause is created explicitly — is what makes "one
    running session per user" true. Enforcing it in Python would be a
    read-then-write that two rapid clicks interleave.
    """

    __tablename__ = "sessions"
    __table_args__ = (
        # Declared here as well as created in migration 0004, so autogenerate
        # sees them in the metadata. Left out, `alembic check` reports drift
        # and the next generated migration would DROP them.
        Index(
            "one_running_session_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'running' AND deleted_at IS NULL"),
        ),
        Index("ix_sessions_user_id_started_at", "user_id", text("started_at DESC")),
        CheckConstraint(f"kind in ({_KINDS_SQL})", name="kind_valid"),
        CheckConstraint(f"status in ({_STATUSES_SQL})", name="status_valid"),
        CheckConstraint("planned_minutes > 0", name="planned_minutes_positive"),
        # A finished session must have an end, and a running one must not.
        # Without this, a bug that forgot to stamp ended_at would leave a
        # "completed" row that phase 3 could not compute a duration for.
        CheckConstraint(
            "(status = 'running') = (ended_at IS NULL)", name="ended_at_matches_status"
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name="ends_after_start"
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="duration_not_negative",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'work'")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'running'")
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # Frozen at start from the user's settings. Stored rather than derived so
    # a later settings change cannot retroactively rewrite what a past session
    # was asked to be.
    planned_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, default=None)

    note: Mapped[str | None] = mapped_column(Text, default=None)
