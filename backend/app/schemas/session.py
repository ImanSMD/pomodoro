from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.session import SESSION_KINDS, SESSION_STATUSES

SessionKind = Literal["work", "short_break", "long_break"]
SessionStatus = Literal["running", "completed", "cancelled"]

# Explicit raises, not asserts: PYTHONOPTIMIZE=1 strips asserts, and a slim
# production image is exactly where drift would reappear as a 500 from the
# check constraint.
if set(get_args(SessionKind)) != set(SESSION_KINDS):
    raise RuntimeError("SessionKind and models.session.SESSION_KINDS disagree")
if set(get_args(SessionStatus)) != set(SESSION_STATUSES):
    raise RuntimeError("SessionStatus and models.session.SESSION_STATUSES disagree")

NOTE_MAX_LENGTH = 2_000

# No session is a real day's work. Bounds both ways of ending one, so a
# skewed client clock cannot write a year of "focus time" that phase 3 then
# sums — and cannot free the running slot while leaving a completed session
# that overlaps every later one.
MAX_SESSION_MINUTES = 24 * 60
# A little slack for an ordinary clock drift; beyond it the end is in the
# future, which no real session has.
FUTURE_SKEW_ALLOWANCE = timedelta(minutes=5)


class SessionStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    kind: SessionKind = "work"


class SessionFixEnd(BaseModel):
    """Close a session that was forgotten, the way the old CLI's fix-end did.

    Either an explicit end time or a duration — never both, because the two
    would have to agree and there is no sensible rule for which wins.
    """

    model_config = ConfigDict(extra="forbid")

    ended_at: datetime | None = None
    duration_minutes: float | None = Field(default=None, gt=0, le=MAX_SESSION_MINUTES)
    note: str | None = Field(default=None, max_length=NOTE_MAX_LENGTH)

    @model_validator(mode="after")
    def _exactly_one_way_to_end(self) -> "SessionFixEnd":
        given = [f for f in ("ended_at", "duration_minutes") if getattr(self, f) is not None]
        if len(given) > 1:
            raise ValueError("give either ended_at or duration_minutes, not both")
        # "note" set explicitly counts as a change even when it is null —
        # that is how a note gets cleared. Checking `self.note is None` would
        # make removing one impossible.
        if not given and "note" not in self.model_fields_set:
            raise ValueError("nothing to change")

        if self.ended_at is not None:
            if self.ended_at.tzinfo is None:
                # Without an offset there is no way to know which instant is
                # meant, and phase 3 buckets by the user's local day.
                raise ValueError("ended_at must carry a timezone offset")
        return self


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    kind: SessionKind
    status: SessionStatus
    started_at: datetime
    ended_at: datetime | None
    planned_minutes: int
    duration_seconds: int | None
    note: str | None


class ActiveSession(BaseModel):
    """The running session, plus the server's clock.

    server_now is not decoration. The client renders a countdown from
    started_at, and a browser clock that is even a minute off would show the
    wrong remaining time — or a finished timer that never fires. The client
    stores (server_now - its own now) once and corrects every reading by it.
    """

    session: SessionOut | None
    server_now: datetime
