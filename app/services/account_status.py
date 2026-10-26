from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import access_request as crud_access
from app.models.access_request import RequestStatus
from app.models.user import User


async def account_block(db: AsyncSession, user: User) -> tuple[str, str] | None:
    """Why an account can't sign in yet, as (machine code, message). None means it can."""
    if not user.email_verified:
        return "email_unverified", "Verify your email to continue."
    if user.is_active:
        return None
    request = await crud_access.get_for_user(db, user.id)
    if request and request.status == RequestStatus.PENDING:
        return "pending_approval", "Your access request is waiting for an administrator."
    if request and request.status == RequestStatus.REJECTED:
        return "rejected", "Your access request was declined."
    return "deactivated", "Your account has been deactivated. Contact an administrator."
