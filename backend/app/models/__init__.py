"""Model registry.

Every model must be imported here: Alembic's autogenerate only sees what is
attached to Base.metadata, and a model that is never imported is silently
missing from migrations.
"""

from app.models.base import Base, SoftDelete, Timestamps, UUIDPrimaryKey
from app.models.category import Category
from app.models.refresh_token import RefreshToken
from app.models.task import Task
from app.models.user import User

__all__ = [
    "Base",
    "Category",
    "RefreshToken",
    "SoftDelete",
    "Task",
    "Timestamps",
    "UUIDPrimaryKey",
    "User",
]
