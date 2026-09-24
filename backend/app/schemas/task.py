from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.task import TASK_STATUSES

DESCRIPTION_MAX_LENGTH = 10_000

TaskStatus = Literal["active", "done"]

# Fails at import if the Literal and the database check constraint drift
# apart — previously the vocabulary was written out in three places and
# adding a status would have updated one or two of them, surfacing as a 500
# from the constraint.
# An explicit raise, not an assert: PYTHONOPTIMIZE=1 strips asserts, and a
# slim production image is exactly where the drift would then reappear as a
# 500 from ck_tasks_status_valid.
if set(get_args(TaskStatus)) != set(TASK_STATUSES):
    raise RuntimeError(
        f"TaskStatus {get_args(TaskStatus)} and "
        f"models.task.TASK_STATUSES {TASK_STATUSES} disagree"
    )


def _trimmed_or_none(value: str | None) -> str | None:
    """Trim, or pass None straight through.

    The None guard matters: this runs on `str | None` for TaskUpdate, and
    pydantic only turns ValueError/AssertionError into a 422 — an
    AttributeError from .strip() on None escapes as a 500.
    """
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("title cannot be blank")
    return trimmed


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    # Bounded like every other free-text field here. It is unbounded Text in
    # the database and there is no body-size limit in front of the app yet, so
    # one oversized row would be serialised into every list response for that
    # user from then on.
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)
    category_id: UUID | None = None

    _title = field_validator("title")(_trimmed_or_none)


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)
    category_id: UUID | None = None

    _title = field_validator("title")(_trimmed_or_none)

    @model_validator(mode="after")
    def _title_cannot_be_cleared(self) -> "TaskUpdate":
        # description and category_id are nullable columns, so an explicit
        # null clears them. title is NOT NULL, so null is a client error.
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title cannot be null")
        return self


class TaskReorder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Bounded: unbounded input on an authenticated endpoint is still input
    # the server has to hold and hash. 1000 is far past any real list.
    ids: list[UUID] = Field(min_length=1, max_length=1000)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category_id: UUID | None
    title: str
    description: str | None
    status: TaskStatus
    position: int
    archived_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
