from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentUser, get_owned_or_404, scoped
from app.db import DbSession
from app.models import Category, Task, User
from app.schemas.category import CategoryCreate, CategoryOut, CategoryUpdate

router = APIRouter(prefix="/api/categories", tags=["categories"])

def _duplicate_name(archived: bool = False) -> HTTPException:
    """Fresh per call — see credentials_exception() in core/deps.py.

    Re-raising one module-level instance appends to its traceback every time
    and never collects it, and two concurrent requests would mutate the same
    object while one is being formatted.

    The archived variant matters: the uniqueness index covers archived rows,
    which the default listing omits, so a bare "already exists" would point at
    a category the user cannot see and has no obvious way to find.
    """
    detail = (
        "An archived category already uses that name; restore or rename it"
        if archived
        else "A category with that name already exists"
    )
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


async def _name_is_archived(db: DbSession, user: User, name: str | None) -> bool:
    """Is the name already taken by a category this user has archived?

    Called BEFORE the insert, deliberately. Querying on the IntegrityError
    path instead would mean issuing a statement on a session whose flush has
    just failed, which behaves differently depending on whether the session
    owns its transaction or has joined an outer one — a difference that shows
    up only under the test harness. One indexed SELECT on a rare write is the
    cheaper trade.

    The unique index remains the authority; this only decides which of two
    409 messages to send.
    """
    if name is None:
        return False
    conflict = await db.scalar(
        scoped(select(Category), Category, user).where(
            func.lower(Category.name) == name.strip().lower(),
            Category.archived_at.is_not(None),
        )
    )
    return conflict is not None


@router.get("")
async def list_categories(
    user: CurrentUser,
    db: DbSession,
    include_archived: bool = Query(default=False),
) -> list[CategoryOut]:
    stmt = scoped(select(Category), Category, user)
    if not include_archived:
        stmt = stmt.where(Category.archived_at.is_(None))
    stmt = stmt.order_by(Category.position, Category.created_at)

    return [CategoryOut.model_validate(c) for c in await db.scalars(stmt)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_category(
    body: CategoryCreate, user: CurrentUser, db: DbSession
) -> CategoryOut:
    # Append to the end of this user's list. COALESCE so the first row starts
    # at 0 rather than NULL.
    #
    # Not atomic: two concurrent creates read the same max and land on the
    # same position. Left as is deliberately — the consequence is that the
    # created_at tiebreak in the ORDER BY decides which comes first, and the
    # next reorder resolves it. Serialising every create per user to avoid a
    # cosmetic tie is the worse trade.
    next_position = await db.scalar(
        scoped(select(func.coalesce(func.max(Category.position) + 1, 0)), Category, user)
    )

    archived_conflict = await _name_is_archived(db, user, body.name)

    category = Category(
        user_id=user.id, position=next_position or 0, **body.model_dump()
    )
    db.add(category)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if "uq_categories_user_id_lower_name" not in str(exc.orig):
            raise
        raise _duplicate_name(archived_conflict) from None

    await db.commit()
    return CategoryOut.model_validate(category)


@router.patch("/{category_id}")
async def update_category(
    category_id: UUID, body: CategoryUpdate, user: CurrentUser, db: DbSession
) -> CategoryOut:
    category = await get_owned_or_404(db, Category, category_id, user)
    archived_conflict = await _name_is_archived(db, user, body.name)

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(category, field, value)

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if "uq_categories_user_id_lower_name" not in str(exc.orig):
            raise
        raise _duplicate_name(archived_conflict) from None

    await db.commit()
    await db.refresh(category)
    return CategoryOut.model_validate(category)


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(category_id: UUID, user: CurrentUser, db: DbSession) -> None:
    category = await get_owned_or_404(db, Category, category_id, user)
    category.deleted_at = datetime.now(timezone.utc)

    # ON DELETE SET NULL never fires for a soft delete — the row still exists.
    # Without this the tasks keep a category_id no endpoint will resolve, so
    # the UI renders a category it cannot name; and because the name is then
    # free to reuse, a new category with the same name leaves those tasks
    # attached to the dead one, invisible under both.
    await db.execute(
        update(Task)
        .where(Task.user_id == user.id, Task.category_id == category.id)
        .values(category_id=None)
    )
    await db.commit()


@router.post("/{category_id}/archive")
async def archive_category(
    category_id: UUID, user: CurrentUser, db: DbSession
) -> CategoryOut:
    category = await get_owned_or_404(db, Category, category_id, user)
    # Decided in the WHERE clause, not by a pre-check — see _apply_once in
    # the tasks router for why a read-then-write does not survive two rapid
    # clicks.
    await db.execute(
        update(Category)
        .where(Category.id == category.id, Category.archived_at.is_(None))
        .values(archived_at=func.now())
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    await db.refresh(category)
    return CategoryOut.model_validate(category)


@router.post("/{category_id}/restore")
async def restore_category(
    category_id: UUID, user: CurrentUser, db: DbSession
) -> CategoryOut:
    category = await get_owned_or_404(db, Category, category_id, user)

    # Guarded like archive: restoring something that was never archived must
    # not reshuffle the sidebar.
    if category.archived_at is not None:
        category.archived_at = None
        # Append rather than resume its old slot — same reasoning as restoring
        # a task: the list has probably moved on. And as there, max+1 is not
        # atomic; the tie it can produce is cosmetic and a reorder clears it.
        category.position = (
            await db.scalar(
                scoped(
                    select(func.coalesce(func.max(Category.position) + 1, 0)),
                    Category,
                    user,
                )
            )
            or 0
        )
        await db.commit()
    return CategoryOut.model_validate(category)
