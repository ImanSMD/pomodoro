from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _trimmed_optional(value: str | None) -> str | None:
    """Trim, keeping None as None and turning empty into None.

    color and icon are nullable, so "  " means the user cleared the field
    rather than set it to whitespace.
    """
    if value is None:
        return None
    return value.strip() or None


def _trimmed_or_none(value: str | None) -> str | None:
    """Trim, or pass None straight through.

    The None guard matters: these validators run on `str | None` fields, and
    pydantic only turns ValueError/AssertionError into a 422 — an
    AttributeError from calling .strip() on None escapes as a 500.
    """
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        # Mirrors ck_categories_name_not_blank: whitespace-only passes
        # min_length and would otherwise fail in the database.
        raise ValueError("name cannot be blank")
    return trimmed


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    color: str | None = Field(default=None, max_length=32)
    icon: str | None = Field(default=None, max_length=64)

    _name = field_validator("name")(_trimmed_or_none)
    _optional = field_validator("color", "icon")(_trimmed_optional)


class CategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = Field(default=None, max_length=32)
    icon: str | None = Field(default=None, max_length=64)

    _name = field_validator("name")(_trimmed_or_none)
    _optional = field_validator("color", "icon")(_trimmed_optional)

    @model_validator(mode="after")
    def _name_cannot_be_cleared(self) -> "CategoryUpdate":
        # color and icon are nullable columns, so an explicit null clears
        # them. name is NOT NULL, so null is a client error, not a 500.
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        return self


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    color: str | None
    icon: str | None
    position: int
    archived_at: datetime | None
    created_at: datetime
