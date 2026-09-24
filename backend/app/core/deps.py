"""Request-scoped dependencies.

get_current_user is the single gate every authenticated route passes through,
and CurrentUser is the annotation that makes using it hard to forget.
"""

from __future__ import annotations

from typing import Annotated, Any, Protocol, TypeVar
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from app.core.security import decode_access_token
from app.db import DbSession
from app.models import User


class _Owned(Protocol):
    """Structural type for a row a user owns and can soft-delete."""

    id: Any
    user_id: Any
    deleted_at: Any


OwnedModel = TypeVar("OwnedModel", bound=_Owned)

# auto_error=False so a missing header produces our own 401 with a
# WWW-Authenticate challenge, rather than FastAPI's bare 403.
_bearer = HTTPBearer(auto_error=False)

def credentials_exception() -> HTTPException:
    """A fresh exception per call, deliberately.

    Python appends to `exc.__traceback__` every time an existing exception
    object is re-raised. A module-level instance therefore accumulates frames
    without bound — measured at 5 -> 250 frames over 50 unauthenticated
    requests — and every retained frame pins its locals, including the
    presented token and the database session. Any anonymous caller could grow
    the process indefinitely.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    connection: HTTPConnection,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise credentials_exception()

    settings = connection.app.state.settings
    try:
        payload = decode_access_token(credentials.credentials, settings.jwt_secret)
    except jwt.PyJWTError:
        # Every failure — expired, wrong signature, wrong type, malformed —
        # returns the same 401. Distinguishing them tells an attacker which
        # part of a forged token to fix.
        raise credentials_exception() from None

    user = await db.scalar(select(User).where(User.id == payload["sub"]))
    if user is None:
        # A valid signature over a deleted user's id.
        raise credentials_exception()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def scoped(stmt: Select[Any], model: type[OwnedModel], user: User) -> Select[Any]:
    """Restrict a statement to one user's live rows.

    Every read of an owned table goes through this. Two predicates, both easy
    to forget individually and serious when forgotten: without `user_id ==`
    one account reads another's data, and without the deleted_at check a
    soft-deleted row comes back from the dead.

    Cover it with the cross-user test rather than relying on discipline — the
    failure is silent in both directions.
    """
    return stmt.where(model.user_id == user.id, model.deleted_at.is_(None))


async def get_owned_or_404(
    db: AsyncSession, model: type[OwnedModel], obj_id: UUID, user: User
) -> OwnedModel:
    """Fetch one row the caller owns, or 404.

    404 and not 403, deliberately: a 403 confirms that the id exists, which
    turns any detail endpoint into an oracle for enumerating other people's
    rows.
    """
    obj = await db.scalar(scoped(select(model), model, user).where(model.id == obj_id))
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return obj
