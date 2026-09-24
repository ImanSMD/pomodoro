from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import CurrentUser
from app.db import DbSession
from app.schemas.user import SettingsOut, SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def read_settings(user: CurrentUser) -> SettingsOut:
    return SettingsOut.model_validate(user)


@router.patch("")
async def update_settings(
    body: SettingsUpdate, user: CurrentUser, db: DbSession
) -> SettingsOut:
    # exclude_unset, not exclude_none: a PATCH that omits a field must leave it
    # alone, and every field here defaults to None — without this, one changed
    # setting would reset all the others.
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)
    return SettingsOut.model_validate(user)
