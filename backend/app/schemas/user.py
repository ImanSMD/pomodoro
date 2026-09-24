from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import available_timezones

from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CalendarPref = Literal["jalali", "gregorian"]


# zoneinfo exposes two names that must never reach the database as a stored
# preference. Postgres rejects "localtime" outright (`AT TIME ZONE 'localtime'`
# is an error), so accepting it would poison every phase-3 analytics query for
# that user — the exact late SQL failure this validator was written to prevent.
# "Factory" is a placeholder meaning "no zone configured" and is meaningless
# as a user setting.
_UNUSABLE_TIMEZONES = frozenset({"localtime", "Factory"})


@lru_cache(maxsize=1)
def _known_timezones() -> frozenset[str]:
    # Cached: available_timezones() walks the tzdata tree and measured ~4.8ms,
    # which would otherwise block the event loop on every settings PATCH.
    return frozenset(available_timezones()) - _UNUSABLE_TIMEZONES


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str
    timezone: str
    calendar_pref: CalendarPref
    created_at: datetime


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timezone: str
    calendar_pref: CalendarPref
    default_work_minutes: int
    default_break_minutes: int
    long_break_minutes: int
    rounds_before_long_break: int
    auto_start_breaks: bool
    sound_enabled: bool
    notifications_enabled: bool


class SettingsUpdate(BaseModel):
    """Every field optional — this is a PATCH.

    Durations are bounded rather than merely positive: phase 3 buckets sessions
    by local day, and a "work" session of 40000 minutes would span several of
    them, so the domain caps it at 24 hours.
    """

    # extra="forbid": a misspelled field would otherwise validate, match
    # nothing, change nothing, and return 200 — the client could not tell.
    model_config = ConfigDict(extra="forbid")

    timezone: str | None = None
    calendar_pref: CalendarPref | None = None
    default_work_minutes: int | None = Field(default=None, ge=1, le=1440)
    default_break_minutes: int | None = Field(default=None, ge=1, le=1440)
    long_break_minutes: int | None = Field(default=None, ge=1, le=1440)
    rounds_before_long_break: int | None = Field(default=None, ge=1, le=100)
    auto_start_breaks: bool | None = None
    sound_enabled: bool | None = None
    notifications_enabled: bool | None = None

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str | None) -> str | None:
        # Validated against the tz database, because phase 3 passes this
        # straight into `AT TIME ZONE` — an unknown name would surface much
        # later as a SQL error inside an analytics query.
        if value is not None and value not in _known_timezones():
            raise ValueError(f"unknown timezone: {value!r}")
        return value

    @model_validator(mode="after")
    def _reject_explicit_nulls(self) -> "SettingsUpdate":
        """None means "not supplied", never "set this column to NULL".

        Every field here is optional so the PATCH can omit it, but no column
        behind them is nullable — so an explicit {"timezone": null} would be
        "set", survive exclude_unset, and hit a NOT NULL violation as a 500.
        """
        nulls = sorted(f for f in self.model_fields_set if getattr(self, f) is None)
        if nulls:
            raise ValueError(f"these fields cannot be null: {', '.join(nulls)}")
        return self
