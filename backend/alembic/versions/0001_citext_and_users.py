"""citext extension and users table

Revision ID: 0001
Revises:
Create Date: 2026-09-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Autogenerate does not emit this: it sees the CITEXT column type but has no
    # idea the extension providing it has to exist first. Without this line
    # `alembic upgrade head` fails on any clean database.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    op.create_table(
        "users",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column(
            "timezone",
            sa.String(length=64),
            server_default=sa.text("'Asia/Tehran'"),
            nullable=False,
        ),
        sa.Column(
            "calendar_pref",
            sa.String(length=16),
            server_default=sa.text("'jalali'"),
            nullable=False,
        ),
        sa.Column(
            "default_work_minutes",
            sa.Integer(),
            server_default=sa.text("25"),
            nullable=False,
        ),
        sa.Column(
            "default_break_minutes",
            sa.Integer(),
            server_default=sa.text("5"),
            nullable=False,
        ),
        sa.Column(
            "long_break_minutes",
            sa.Integer(),
            server_default=sa.text("15"),
            nullable=False,
        ),
        sa.Column(
            "rounds_before_long_break",
            sa.Integer(),
            server_default=sa.text("4"),
            nullable=False,
        ),
        sa.Column(
            "auto_start_breaks",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "sound_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "notifications_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Names come from Base.metadata's naming convention, so later migrations
        # can drop them by a name autogenerate can predict.
        sa.CheckConstraint("length(email) <= 320", name=op.f("ck_users_email_length")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )


def downgrade() -> None:
    op.drop_table("users")
    # The extension is deliberately left in place. It is database-wide and
    # something else may already depend on it; dropping it to undo one
    # migration is a bigger blast radius than the migration itself.
