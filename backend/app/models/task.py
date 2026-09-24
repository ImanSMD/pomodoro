from __future__ import annotations

from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDelete, Timestamps, UUIDPrimaryKey

# The canonical vocabulary. schemas/task.py asserts its Literal matches this,
# so the three places that used to spell it out independently cannot drift.
TASK_STATUSES: Final = ("active", "done")
_STATUS_SQL_LIST = ", ".join(f"'{s}'" for s in TASK_STATUSES)


class Task(UUIDPrimaryKey, Timestamps, SoftDelete, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(f"status in ({_STATUS_SQL_LIST})", name="status_valid"),
        CheckConstraint("char_length(trim(title)) > 0", name="title_not_blank"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SET NULL rather than CASCADE: deleting a category must not take its tasks
    # (and their logged sessions) with it. The task becomes uncategorised.
    category_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        default=None,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'active'")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
