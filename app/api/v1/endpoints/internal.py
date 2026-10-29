"""Calls made only by the Node gateway, authenticated with a shared key (never reachable from a browser)."""

import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import settings
from app.core.security import create_access_token
from app.crud import access_request as crud_access
from app.crud import notification as crud_notification
from app.crud import user as crud_user
from app.schemas.access import AdminContact, EmailBody, LoginLookup, PasswordSet, SignupStatus, VerifiedResult
from app.schemas.token import Token
from app.schemas.user import Preferences, UserBrief
from app.services.account_status import account_block

# Service tokens only need to outlive the request that asked for one; the gateway caches them for less than this.
SERVICE_TOKEN_TTL = timedelta(minutes=2)


async def require_internal_key(x_internal_key: str | None = Header(default=None)) -> None:
    if x_internal_key is None or not secrets.compare_digest(x_internal_key.encode(), settings.INTERNAL_API_KEY.encode()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Forbidden")


router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal_key)], include_in_schema=False)


@router.post("/users/{user_id}/token", response_model=Token)
async def service_token(user_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Token:
    """A short-lived bearer token acting as this user, so the gateway never holds the JWT signing secret."""
    user = await crud_user.get(db, user_id)
    if user is None or not user.is_active or not user.email_verified:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No active user")
    user_role = user.role.value if hasattr(user.role, "value") else str(user.role)
    return Token(access_token=create_access_token(str(user.id), user_role, SERVICE_TOKEN_TTL))


@router.post("/login/lookup", response_model=LoginLookup)
async def login_lookup(data: EmailBody, db: AsyncSession = Depends(get_db)) -> LoginLookup:
    """Can this email sign in right now? Lets the gateway decide whether to send a sign-in or reset code."""
    user = await crud_user.get_by_email(db, data.email)
    if user is None:
        return LoginLookup(status="unknown")
    block = await account_block(db, user)
    return LoginLookup(status=block[0] if block else "ok", id=user.id, full_name=user.full_name)


@router.post("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def set_password(user_id: uuid.UUID, data: PasswordSet, db: AsyncSession = Depends(get_db)) -> Response:
    """Set a new password after the gateway verified a reset code sent to the account's own email."""
    user = await crud_user.get(db, user_id)
    if user is None or not user.email_verified or not user.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No active user")
    await crud_user.set_password(db, user, data.password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/signup/status", response_model=SignupStatus)
async def signup_status(data: EmailBody, db: AsyncSession = Depends(get_db)) -> SignupStatus:
    user = await crud_user.get_by_email(db, data.email)
    if user is None:
        return SignupStatus(exists=False)
    return SignupStatus(exists=True, email_verified=user.email_verified, full_name=user.full_name)


@router.post("/signup/verified", response_model=VerifiedResult)
async def signup_verified(data: EmailBody, db: AsyncSession = Depends(get_db)) -> VerifiedResult:
    """The gateway confirmed the OTP. Opens the access request and notifies admins (once; repeats are no-ops)."""
    user = await crud_user.get_by_email(db, data.email)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No account for that email")
    brief = UserBrief.model_validate(user)
    if user.email_verified:
        existing = await crud_access.get_for_user(db, user.id)
        return VerifiedResult(user=brief, auto_approved=user.is_active, request_id=existing.id if existing else None, admins=[])

    user.email_verified = True
    if user.is_active:  # SIGNUP_AUTO_ACTIVATE: nothing for an admin to decide
        await db.commit()
        return VerifiedResult(user=brief, auto_approved=True, admins=[])

    request = await crud_access.open_for(db, user, organization_id=user.organization_id)
    admins = await crud_user.list_admins(db, organization_id=user.organization_id)
    who = user.full_name or user.email
    crud_notification.add_for_all(
        db, admins, "access_requested", "New access request", f"{who} is asking for access.", "/users?tab=requests", organization_id=user.organization_id
    )
    await db.commit()
    return VerifiedResult(
        user=brief,
        auto_approved=False,
        request_id=request.id,
        admins=[
            AdminContact(
                email=a.email,
                full_name=a.full_name,
                wants_email=Preferences.model_validate(a.preferences or {}).notify_email_access_requests,
            )
            for a in admins
        ],
    )
