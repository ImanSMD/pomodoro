from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

# Matches the ck_users_email_length constraint. Without it an over-long address
# reaches Postgres and fails as a 500 instead of a 422.
EMAIL_MAX_LENGTH = 320
PASSWORD_MIN_LENGTH = 8
# Argon2 hashes the input, so there is no bcrypt-style 72-byte truncation, but
# an unbounded password is a cheap denial-of-service: every login would hash
# megabytes.
PASSWORD_MAX_LENGTH = 128


class RegisterRequest(BaseModel):
    email: EmailStr = Field(max_length=EMAIL_MAX_LENGTH)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    display_name: str = Field(min_length=1, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr = Field(max_length=EMAIL_MAX_LENGTH)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
