"""Model registry.

Every model must be imported here: Alembic's autogenerate only sees what is
attached to Base.metadata, and a model that is never imported is silently
missing from migrations.
"""

from app.models.base import Base, SoftDelete, Timestamps, UUIDPrimaryKey
from app.models.user import User

__all__ = ["Base", "SoftDelete", "Timestamps", "UUIDPrimaryKey", "User"]
