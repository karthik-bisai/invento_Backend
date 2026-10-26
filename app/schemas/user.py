import re
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.core.config import settings
from app.models.user import Role


def _fits_bcrypt(password: str) -> str:
    if len(password.encode()) > 72:
        raise ValueError("Password must be 72 bytes or fewer (some characters take more than one byte)")
    return password


Password = Annotated[str, Field(min_length=8), AfterValidator(_fits_bcrypt)]

_PHONE = re.compile(r"^[0-9+()\-\s.]{5,32}$")


def _blank_to_none(value: str | None) -> str | None:
    """Forms send "" to mean "clear this field"."""
    if value is None:
        return None
    value = value.strip()
    return value or None


class Preferences(BaseModel):
    """Per-user settings. Missing keys fall back to these defaults, so old rows keep working."""

    theme: Literal["system", "light", "dark"] = "system"
    compact_tables: bool = False
    reduce_motion: bool = False
    notify_low_stock: bool = True  # show low-stock alerts in the bell
    notify_email_access_requests: bool = True  # admins: email when someone asks for access


class PreferencesUpdate(BaseModel):
    theme: Literal["system", "light", "dark"] | None = None
    compact_tables: bool | None = None
    reduce_motion: bool | None = None
    notify_low_stock: bool | None = None
    notify_email_access_requests: bool | None = None


class UserCreate(BaseModel):
    email: EmailStr
    password: Password
    role: Role = Role.WAREHOUSE_STAFF
    full_name: str | None = Field(default=None, max_length=120)

    _clean_name = field_validator("full_name")(_blank_to_none)


class UserRegister(BaseModel):
    email: EmailStr
    password: Password
    full_name: str | None = Field(default=None, max_length=120)

    _clean_name = field_validator("full_name")(_blank_to_none)


class UserUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
    password: Password | None = None


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=32)

    _clean = field_validator("full_name", "job_title", "phone")(_blank_to_none)

    @field_validator("phone")
    @classmethod
    def _phone_shape(cls, value: str | None) -> str | None:
        if value is not None and not _PHONE.match(value):
            raise ValueError("Enter a phone number using digits, spaces, + ( ) - .")
        return value


class AvatarUpdate(BaseModel):
    avatar_url: str | None = Field(default=None, max_length=500)

    @field_validator("avatar_url")
    @classmethod
    def _own_media_only(cls, value: str | None) -> str | None:
        value = _blank_to_none(value)
        if value is not None and not value.startswith(settings.MEDIA_PUBLIC_URL):
            raise ValueError("Avatars must be uploaded through the app")
        return value


class PasswordChange(BaseModel):
    current_password: str
    new_password: Password

    @model_validator(mode="after")
    def _must_differ(self) -> "PasswordChange":
        if self.current_password == self.new_password:
            raise ValueError("Choose a password you haven't used here")
        return self


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    avatar_url: str | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: Role
    is_active: bool
    email_verified: bool = True
    full_name: str | None = None
    job_title: str | None = None
    phone: str | None = None
    avatar_url: str | None = None
    preferences: Preferences = Field(default_factory=Preferences)
    created_at: datetime | None = None
