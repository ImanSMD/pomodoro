from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select, true
from sqlalchemy.exc import IntegrityError
from starlette.requests import HTTPConnection

from app.core.deps import CurrentUser, get_owned_or_404, scoped
from app.core.mutations import apply_once
from app.db import DbSession
from app.models import Session, Task, User
from app.schemas.session import (
    COMPLETE_GRACE,
    FUTURE_SKEW_ALLOWANCE,
    MAX_SESSION_MINUTES,
    ActiveSession,
    SessionFixEnd,
    SessionOut,
    SessionStart,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

# Which user setting supplies the length of each kind of session. Frozen into
# planned_minutes at start, so changing a default later cannot rewrite what a
# past session was asked to be.
_MINUTES_FIELD = {
    "work": "default_work_minutes",
    "short_break": "default_break_minutes",
    "long_break": "long_break_minutes",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _running(user: User):
    return scoped(select(Session), Session, user).where(Session.status == "running")


async def _broadcast(connection: HTTPConnection, user: User, event: str, session: Session) -> None:
    await connection.app.state.ws_manager.broadcast(
        user.id, event, SessionOut.model_validate(session).model_dump(mode="json")
    )


@router.post("/start", status_code=status.HTTP_201_CREATED)
async def start_session(
    body: SessionStart, user: CurrentUser, db: DbSession, connection: HTTPConnection
) -> SessionOut:
    # Ownership of the task, and 404 if it is not this user's.
    await get_owned_or_404(db, Task, body.task_id, user)

    session = Session(
        user_id=user.id,
        task_id=body.task_id,
        kind=body.kind,
        started_at=_now(),
        planned_minutes=getattr(user, _MINUTES_FIELD[body.kind]),
    )
    db.add(session)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        # The partial unique index is the check, not a prior SELECT. Two rapid
        # clicks both pass a read-then-write test and one then 500s.
        if "one_running_session_per_user" not in str(exc.orig):
            raise
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A session is already running",
        ) from None

    await db.commit()
    await db.refresh(session)
    await _broadcast(connection, user, "session.started", session)
    return SessionOut.model_validate(session)


@router.get("/active")
async def active_session(user: CurrentUser, db: DbSession) -> ActiveSession:
    session = await db.scalar(_running(user))
    # server_now travels with the session so the client can correct for a
    # skewed browser clock; see ActiveSession.
    return ActiveSession(
        session=SessionOut.model_validate(session) if session else None,
        server_now=_now(),
    )


@router.get("")
async def list_sessions(
    user: CurrentUser,
    db: DbSession,
    task_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[SessionOut]:
    stmt = scoped(select(Session), Session, user)
    if task_id is not None:
        stmt = stmt.where(Session.task_id == task_id)
    stmt = stmt.order_by(Session.started_at.desc()).limit(limit)
    return [SessionOut.model_validate(s) for s in await db.scalars(stmt)]


@router.post("/{session_id}/complete")
async def complete_session(
    session_id: UUID, user: CurrentUser, db: DbSession, connection: HTTPConnection
) -> SessionOut:
    session = await get_owned_or_404(db, Session, session_id, user)
    ended = _now()

    overdue_by = ended - (
        session.started_at + timedelta(minutes=session.planned_minutes)
    )
    if session.status == "running" and overdue_by > COMPLETE_GRACE:
        # Too late to just claim it. The elapsed time is no longer evidence of
        # work: a session left running with every tab closed would record the
        # whole night. Say when it actually ended instead.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This session is more than "
                f"{int(COMPLETE_GRACE.total_seconds() // 60)} minutes past its "
                "planned end. Set its end time with PATCH, or cancel it."
            ),
        )

    # Idempotent, and decided in the WHERE clause: a retry or a double-click
    # must not move ended_at, which phase 3 buckets by.
    applied = await apply_once(
        db,
        session,
        Session.status == "running",
        status="completed",
        ended_at=ended,
        # Recomputed from the timestamps rather than trusted from the client:
        # a tab that was asleep when the countdown hit zero completes late,
        # and the duration must be what actually elapsed.
        duration_seconds=int((ended - session.started_at).total_seconds()),
    )
    if applied:
        await _broadcast(connection, user, "session.completed", session)
    return SessionOut.model_validate(session)


@router.post("/{session_id}/cancel")
async def cancel_session(
    session_id: UUID, user: CurrentUser, db: DbSession, connection: HTTPConnection
) -> SessionOut:
    session = await get_owned_or_404(db, Session, session_id, user)
    ended = _now()

    applied = await apply_once(
        db,
        session,
        Session.status == "running",
        status="cancelled",
        ended_at=ended,
        # Cancelled sessions carry a duration but are excluded from totals in
        # phase 3 — the time is recorded, it just does not count as focus.
        duration_seconds=int((ended - session.started_at).total_seconds()),
    )
    if applied:
        await _broadcast(connection, user, "session.cancelled", session)
    return SessionOut.model_validate(session)


@router.patch("/{session_id}")
async def fix_session_end(
    session_id: UUID,
    body: SessionFixEnd,
    user: CurrentUser,
    db: DbSession,
    connection: HTTPConnection,
) -> SessionOut:
    """Close a session that was forgotten, or annotate one.

    The old CLI's `fix-end`: you walked away without stopping the timer, so
    you say when it really ended.
    """
    session = await get_owned_or_404(db, Session, session_id, user)

    values: dict = {}
    # Explicitly set, even to null: that is how a note is cleared.
    if "note" in body.model_fields_set:
        values["note"] = body.note

    ended: datetime | None = None
    if body.duration_minutes is not None:
        ended = session.started_at + timedelta(minutes=body.duration_minutes)
    elif body.ended_at is not None:
        ended = body.ended_at

    if ended is not None:
        if ended < session.started_at:
            # Also a check constraint, but caught here so it is a 422 with an
            # explanation rather than a 500 from the database.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="ended_at cannot be before the session started",
            )
        # Checked on the computed end, so both ways of ending are bounded
        # alike: "it ran 45 minutes" on a session started two minutes ago is
        # the same claim as an ended_at in the future, and fix-end is
        # retrospective — you say when it *did* end.
        if ended > _now() + FUTURE_SKEW_ALLOWANCE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="a session cannot end in the future",
            )
        elapsed = (ended - session.started_at).total_seconds()
        if elapsed > MAX_SESSION_MINUTES * 60:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"a session cannot last more than {MAX_SESSION_MINUTES // 60} hours",
            )
        values.update(
            status="completed", ended_at=ended, duration_seconds=int(elapsed)
        )

    # The precondition rides in the WHERE clause rather than an `if
    # session.status == "cancelled"` above: that is a read-then-write, and a
    # fix-end racing the timer's own completion would interleave between the
    # two. Note-only edits are unconditional.
    condition = Session.status != "cancelled" if ended is not None else true()
    applied = await apply_once(db, session, condition, **values)

    if ended is not None and not applied:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A cancelled session cannot be given an end time",
        )
    if ended is not None:
        await _broadcast(connection, user, "session.completed", session)
    return SessionOut.model_validate(session)
