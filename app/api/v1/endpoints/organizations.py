from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_roles
from app.crud import organization as crud_org
from app.models.user import Role, User
from app.schemas.organization import OrganizationLookup, OrganizationResponse, OrganizationUpdate

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("/lookup/{org_code}", response_model=OrganizationLookup)
async def lookup_organization(org_code: str, db: AsyncSession = Depends(get_db)) -> OrganizationLookup:
    """Public lookup to validate Organization ID before employee sign-up."""
    org = await crud_org.get_by_code(db, org_code)
    if org is None:
        return OrganizationLookup(exists=False)
    return OrganizationLookup(exists=True, name=org.name, org_code=org.org_code)


@router.get("/me", response_model=OrganizationResponse)
async def get_my_organization(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrganizationResponse:
    if user.organization_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No organization associated with this account")
    org = await crud_org.get(db, user.organization_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return org


@router.patch("/me", response_model=OrganizationResponse, dependencies=[Depends(require_roles([Role.ADMIN]))])
async def update_my_organization(
    data: OrganizationUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrganizationResponse:
    """Update organization settings, including configurable currency (Admin only)."""
    if user.organization_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No organization associated with this account")
    org = await crud_org.get(db, user.organization_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return await crud_org.update(db, org, data)
