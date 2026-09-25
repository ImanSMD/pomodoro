"""sessions

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-25 16:00:07.857914
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('sessions',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=16), server_default=sa.text("'work'"), nullable=False),
    sa.Column('status', sa.String(length=16), server_default=sa.text("'running'"), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('planned_minutes', sa.Integer(), nullable=False),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(status = 'running') = (ended_at IS NULL)", name=op.f('ck_sessions_ended_at_matches_status')),
    sa.CheckConstraint("kind in ('work', 'short_break', 'long_break')", name=op.f('ck_sessions_kind_valid')),
    sa.CheckConstraint("status in ('running', 'completed', 'cancelled')", name=op.f('ck_sessions_status_valid')),
    sa.CheckConstraint('duration_seconds IS NULL OR duration_seconds >= 0', name=op.f('ck_sessions_duration_not_negative')),
    sa.CheckConstraint('ended_at IS NULL OR ended_at >= started_at', name=op.f('ck_sessions_ends_after_start')),
    sa.CheckConstraint('planned_minutes > 0', name=op.f('ck_sessions_planned_minutes_positive')),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_sessions_task_id_tasks'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sessions'))
    )
    op.create_index(op.f('ix_sessions_deleted_at'), 'sessions', ['deleted_at'], unique=False)
    op.create_index(op.f('ix_sessions_task_id'), 'sessions', ['task_id'], unique=False)
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)

    # One running session per user, enforced by Postgres rather than by
    # application logic. A read-then-write check ("is anything running?" then
    # INSERT) is interleaved by two rapid clicks; this cannot be.
    #
    # deleted_at is in the predicate too: a soft-deleted running row must not
    # keep blocking new sessions the user can no longer see or cancel.
    op.create_index(
        "one_running_session_per_user",
        "sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running' AND deleted_at IS NULL"),
    )

    # Phase 3 buckets by started_at for a single user; both analytics indexes
    # are cheap now and painful to add once the table is large.
    op.create_index(
        "ix_sessions_user_id_started_at",
        "sessions",
        ["user_id", sa.text("started_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_sessions_user_id_started_at", table_name="sessions")
    op.drop_index("one_running_session_per_user", table_name="sessions")
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_index(op.f('ix_sessions_task_id'), table_name='sessions')
    op.drop_index(op.f('ix_sessions_deleted_at'), table_name='sessions')
    op.drop_table('sessions')
