from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# Matches the ck_users_email_length constraint. Without it an over-long address
# reaches Postgres and fails as a 500 instead of a 422.
EMAIL_MAX_LENGTH = 320
PASSWORD_MIN_LENGTH = 8
# Argon2 hashes the input, so there is no bcrypt-style 72-byte truncation, but
# an unbounded password is a cheap denial-of-service: every login would hash
# megabytes.
PASSWORD_MAX_LENGTH = 128


class RegisterRequest(BaseModel):
    # extra="forbid", as on every other input schema in this phase. Without it
    # a sign-up form that posts the browser's detected timezone — the most
    # natural extra field here, and a real column on users — gets a 201 and an
    # account on the Asia/Tehran default, with nothing in the response saying
    # the field was dropped. That value feeds phase 3's AT TIME ZONE day
    # bucketing, so the wrong one is durable and invisible.
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=EMAIL_MAX_LENGTH)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    display_name: str = Field(min_length=1, max_length=100)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=EMAIL_MAX_LENGTH)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
