"""State changes whose precondition is decided by the database.

Extracted from the tasks router in 1.5 so sessions can reuse it rather than
restate the pattern.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession


async def apply_once(
    db: AsyncSession, obj: Any, condition: Any, **values: Any
) -> bool:
    """Apply a state change exactly once. Returns whether it applied.

    An `if obj.status != "done":` pre-check is a read-then-write across two
    statements: two rapid clicks each get their own session, both read the old
    value, and both write — the very case such a guard is usually written to
    prevent. The phase-1 document says as much in 1.5: "Do not pre-check with
    a SELECT — two rapid clicks interleave between the read and the write."

    The condition goes in the WHERE clause instead, so the second request
    matches no rows and changes nothing.
    """
    model = type(obj)
    result = await db.execute(
        update(model)
        .where(model.id == obj.id, condition)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    # synchronize_session=False leaves the in-session object stale, and the
    # response is built from it.
    await db.refresh(obj)
    return bool(result.rowcount)
