from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Integer, String, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPrimaryKey


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"
    __table_args__ = (
        # CITEXT is unbounded and the unique constraint builds a btree over it,
        # so a sufficiently long address fails deep in Postgres with "index row
        # size exceeds maximum" — a 500 instead of a 422. 320 is the RFC 5321
        # maximum. The bound belongs here rather than only in 1.3's Pydantic
        # schema, which any non-HTTP insert path bypasses.
        CheckConstraint("length(email) <= 320", name="email_length"),
    )

    # CITEXT rather than lower()-on-write: case-insensitive uniqueness is
    # enforced by the column type, so no code path can insert a duplicate that
    # differs only in case.
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)

    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'Asia/Tehran'")
    )
    calendar_pref: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'jalali'")
    )

    # Defaults a new task inherits when it sets no override of its own.
    default_work_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("25")
    )
    default_break_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("5")
    )
    long_break_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("15")
    )
    rounds_before_long_break: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("4")
    )

    auto_start_breaks: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    sound_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
