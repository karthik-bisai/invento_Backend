import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.access_request import RequestStatus
from app.models.user import Role
from app.schemas.user import Password, UserBrief


class AccessRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: RequestStatus
    created_at: datetime
    decided_at: datetime | None = None
    note: str | None = None
    user: UserBrief


class ApproveRequest(BaseModel):
    role: Role | str = Role.WAREHOUSE_STAFF


class RejectRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    title: str
    body: str | None = None
    link: str | None = None
    read_at: datetime | None = None
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationResponse]
    unread: int


# ---- gateway-only (/internal) payloads ------------------------------------------------------


class EmailBody(BaseModel):
    email: EmailStr


class SignupStatus(BaseModel):
    exists: bool
    email_verified: bool = False
    full_name: str | None = None


class AdminContact(BaseModel):
    email: EmailStr
    full_name: str | None = None
    wants_email: bool  # their notify_email_access_requests preference


class LoginLookup(BaseModel):
    """Where an account stands, for the gateway's code-based sign-in and password reset."""

    status: str  # "ok", "unknown", or an account_block code: email_unverified, pending_approval, rejected, deactivated
    id: uuid.UUID | None = None
    full_name: str | None = None


class PasswordSet(BaseModel):
    password: Password


class VerifiedResult(BaseModel):
    user: UserBrief
    auto_approved: bool
    request_id: uuid.UUID | None = None
    admins: list[AdminContact]
