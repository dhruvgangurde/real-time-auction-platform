"""Request and response schemas for identity, with explicit limits (DC-11, NFR-19)."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.security import TOKEN_PATTERN

PASSWORD_MIN = 10  # FR-2
PASSWORD_MAX = 128


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(_Strict):
    email: EmailStr = Field(max_length=320)
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)
    full_name: str = Field(min_length=1, max_length=200)


class LoginRequest(_Strict):
    email: EmailStr = Field(max_length=320)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)


class EmailRequest(_Strict):
    email: EmailStr = Field(max_length=320)


class TokenRequest(_Strict):
    token: str = Field(pattern=TOKEN_PATTERN)


class ResetPasswordRequest(_Strict):
    token: str = Field(pattern=TOKEN_PATTERN)
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str
    avatar_url: str | None
    status: str
    roles: tuple[str, ...]
