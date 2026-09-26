from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select, update

from app.core.deps import CurrentUser, get_owned_or_404, scoped
from app.core.mutations import apply_once
from app.db import DbSession
from app.models import Category, Task
from app.models import Session as SessionModel
from app.schemas.task import TaskCreate, TaskOut, TaskReorder, TaskStatus, TaskUpdate

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _has_a_running_session(task: Task):
    """A correlated EXISTS, so the check and the write are one statement.

    The client disables these buttons, but that reads one tab's cache: a
    second device a few seconds behind would still delete the task, leaving a
    session running against something no list shows — unreachable except
    through /sessions/active, and blocking every new start with a 409.

    A SELECT followed by an UPDATE would have the same gap in miniature, which
    is the pattern apply_once exists to avoid.
    """
    return (
        select(SessionModel.id)
        .where(
            SessionModel.task_id == task.id,
            SessionModel.status == "running",
            SessionModel.deleted_at.is_(None),
        )
        .exists()
    )


def _running_session_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A session is running on this task. Finish or cancel it first.",
    )


async def _assert_category_owned(
    db: DbSession, category_id: UUID | None, user: CurrentUser
) -> None:
    """A task may only point at a category the same user owns.

    Without this check the category_id is caller-supplied and unvalidated: the
    foreign key alone would happily accept another user's category id, leaking
    its existence and grouping this task under it.
    """
    if category_id is None:
        return
    # Archived is allowed, deliberately. Archiving is reversible and its whole
    # point is to keep the grouping for later, so an archived category stays
    # referenceable and its tasks keep their link. Refusing here while
    # archive_category left existing tasks attached would have been an
    # invariant claimed and not held.
    #
    # The contract this implies for the client: resolve category names from
    # GET /api/categories?include_archived=true, and offer only live ones in
    # the picker. Recorded in the phase-1 document under 1.6.
    await get_owned_or_404(db, Category, category_id, user)


@router.get("")
async def list_tasks(
    user: CurrentUser,
    db: DbSession,
    category_id: UUID | None = Query(default=None),
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, max_length=200),
    include_archived: bool = Query(default=False),
) -> list[TaskOut]:
    stmt = scoped(select(Task), Task, user)

    if category_id is not None:
        stmt = stmt.where(Task.category_id == category_id)
    if status_filter is not None:
        stmt = stmt.where(Task.status == status_filter)
    if q:
        # ilike with the wildcards escaped: a literal % or _ in the query
        # would otherwise widen the search instead of narrowing it.
        pattern = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Task.title.ilike(f"%{pattern}%", escape="\\"))
    if not include_archived:
        stmt = stmt.where(Task.archived_at.is_(None))

    stmt = stmt.order_by(Task.position, Task.created_at)
    return [TaskOut.model_validate(t) for t in await db.scalars(stmt)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task(body: TaskCreate, user: CurrentUser, db: DbSession) -> TaskOut:
    await _assert_category_owned(db, body.category_id, user)

    # Append to the end of this user's list. Not atomic: two concurrent
    # creates read the same max and tie, after which the created_at tiebreak
    # in the ORDER BY decides and the next reorder resolves it. Serialising
    # every create per user to avoid a cosmetic tie is the worse trade.
    next_position = await db.scalar(
        scoped(select(func.coalesce(func.max(Task.position) + 1, 0)), Task, user)
    )
    task = Task(user_id=user.id, position=next_position or 0, **body.model_dump())
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return TaskOut.model_validate(task)


@router.patch("/reorder", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_tasks(body: TaskReorder, user: CurrentUser, db: DbSession) -> None:
    """Rearrange the submitted tasks among the positions they already occupy.

    A subset is allowed, and that matters: the obvious client renders its
    drag-and-drop list from GET /api/tasks?status=active, so demanding every
    live task (completed ones included) would make the natural call always
    fail.

    Only the submitted rows move. Their current positions are collected and
    handed back out in the new order, so every task not mentioned keeps its
    exact position — a stale client cannot perturb rows it never showed.

    Declared before /{task_id} so "reorder" is matched as a literal path
    rather than attempted as a UUID.
    """
    submitted = list(dict.fromkeys(body.ids))
    if len(submitted) != len(body.ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Duplicate task ids"
        )

    # Every non-deleted row, archived ones sorted after the live list. They
    # take part in the densify so they cannot keep a stale position that
    # collides with a live row — which would leave the archived view ordered
    # by the created_at tiebreak alone, and that does not discriminate rows
    # written in one transaction (now() is the transaction timestamp).
    everything = list(
        await db.scalars(
            scoped(select(Task), Task, user).order_by(
                Task.archived_at.is_not(None), Task.position, Task.created_at
            )
        )
    )
    ordered = [t for t in everything if t.archived_at is None]
    by_id = {task.id: task for task in ordered}

    unknown = [str(i) for i in submitted if i not in by_id]
    if unknown:
        # Covers "does not exist", "soft deleted", "archived" and "belongs to
        # someone else" alike — the caller learns nothing about which.
        shown = ", ".join(unknown[:10])
        more = "" if len(unknown) <= 10 else f" (and {len(unknown) - 10} more)"
        # Truncated: echoing every rejected id turns a large request into a
        # far larger response, and a list that long is unusable to a client.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown task ids: {shown}{more}",
        )

    # Build the final sequence explicitly rather than re-sorting: walk the
    # current order and, at each slot held by a submitted task, take the next
    # id the client asked for. Rows not mentioned stay exactly where they are.
    #
    # Explicit because sorting cannot express the request when positions tie:
    # two rows that collided on creation both sort to the same key, and the
    # created_at tiebreak would silently override the order just requested.
    wanted = iter(submitted)
    submitted_ids = set(submitted)
    rearranged = [by_id[next(wanted)] if t.id in submitted_ids else t for t in ordered]
    final = rearranged + [t for t in everything if t.archived_at is not None]

    # Dense 0..n-1, which is also what repairs a collision — the create path
    # promises the next reorder will.
    for index, task in enumerate(final):
        task.position = index
    await db.commit()


@router.patch("/{task_id}")
async def update_task(
    task_id: UUID, body: TaskUpdate, user: CurrentUser, db: DbSession
) -> TaskOut:
    task = await get_owned_or_404(db, Task, task_id, user)

    updates = body.model_dump(exclude_unset=True)
    # Only when it actually changes. Checking on mere presence locks a client
    # out of editing a task by sending back the object it last read — the
    # normal pattern, and the only thing a stale client can do — once the
    # category it names has been archived or deleted.
    if "category_id" in updates and updates["category_id"] != task.category_id:
        await _assert_category_owned(db, updates["category_id"], user)

    for field, value in updates.items():
        setattr(task, field, value)

    await db.commit()
    await db.refresh(task)
    return TaskOut.model_validate(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: UUID, user: CurrentUser, db: DbSession) -> None:
    task = await get_owned_or_404(db, Task, task_id, user)
    applied = await apply_once(
        db, task, ~_has_a_running_session(task), deleted_at=func.now()
    )
    if not applied:
        raise _running_session_conflict()


@router.post("/{task_id}/archive")
async def archive_task(task_id: UUID, user: CurrentUser, db: DbSession) -> TaskOut:
    task = await get_owned_or_404(db, Task, task_id, user)
    if task.archived_at is not None:
        return TaskOut.model_validate(task)  # already archived, nothing to do

    applied = await apply_once(
        db,
        task,
        Task.archived_at.is_(None) & ~_has_a_running_session(task),
        archived_at=func.now(),
    )
    if not applied:
        raise _running_session_conflict()
    return TaskOut.model_validate(task)


@router.post("/{task_id}/restore")
async def restore_task(task_id: UUID, user: CurrentUser, db: DbSession) -> TaskOut:
    task = await get_owned_or_404(db, Task, task_id, user)

    # Guarded like archive: restoring a task that was never archived must be a
    # no-op. Without this the position rewrite below fires anyway and moves a
    # live task to the end of the user's hand-ordered list.
    if task.archived_at is not None:
        task.archived_at = None
        # Append rather than resume its old slot: the list was very likely
        # reordered while this was archived, so the stale position would tie
        # with whatever now holds it.
        #
        # max+1 here is not atomic either — two restores, or a restore racing
        # a create, can land on the same position. Accepted on the same terms
        # as the create path: the tie is cosmetic, ORDER BY breaks it, and the
        # next reorder densifies it away.
        task.position = (
            await db.scalar(
                scoped(select(func.coalesce(func.max(Task.position) + 1, 0)), Task, user)
            )
            or 0
        )
        await db.commit()
    return TaskOut.model_validate(task)


@router.post("/{task_id}/complete")
async def complete_task(task_id: UUID, user: CurrentUser, db: DbSession) -> TaskOut:
    task = await get_owned_or_404(db, Task, task_id, user)
    # A double-click must not move completed_at forward — phase 3 buckets by
    # completion day.
    await apply_once(
        db, task, Task.status != "done", status="done", completed_at=func.now()
    )
    return TaskOut.model_validate(task)


@router.post("/{task_id}/reopen")
async def reopen_task(task_id: UUID, user: CurrentUser, db: DbSession) -> TaskOut:
    task = await get_owned_or_404(db, Task, task_id, user)
    await apply_once(
        db, task, Task.status != "active", status="active", completed_at=None
    )
    return TaskOut.model_validate(task)
