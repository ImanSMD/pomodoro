"""Declarative base and the mixins nearly every table uses.

Soft delete is a mixin rather than a base-class default because a few tables
(the audit log in phase 2) are append-only and must never be soft-deleted.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, MetaData, func, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names, settled now because it cannot be settled
# later. Phases 1.4 and 1.5 add partial unique indexes, foreign keys and more
# unique constraints; without a convention Postgres assigns names that
# autogenerate cannot predict, so a later op.drop_constraint either fails or
# silently targets nothing. Retrofitting means renaming live constraints.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKey:
    # server_default too, not just the Python-side default: an INSERT from a
    # data migration, seed script or psql would otherwise fail on NOT NULL
    # rather than generating a key. gen_random_uuid() is built into Postgres 13+.
    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )


class Timestamps:
    # server_default so rows INSERTed outside the ORM (migrations, psql) are
    # still stamped, and timezone=True everywhere — phase 3's analytics bucket
    # by the user's local day and cannot do that from naive timestamps.
    #
    # Note the asymmetry: onupdate below is SQLAlchemy-level only, with no
    # database trigger behind it, so a raw UPDATE from psql or a data migration
    # leaves updated_at stale. Anything keying off "changed since" must go
    # through the ORM, or this needs a trigger.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDelete:
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )
