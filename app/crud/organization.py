import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.base import save
from app.models.organization import Organization
from app.schemas.organization import OrganizationUpdate

_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


async def generate_unique_org_code(db: AsyncSession) -> str:
    """Generate a readable, non-sequential unique organization ID (e.g. ORG-7K8M2P4X)."""
    for _ in range(50):
        suffix = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
        code = f"ORG-{suffix}"
        existing = await db.scalar(select(Organization.id).where(Organization.org_code == code))
        if not existing:
            return code
    # Fallback to UUID hex slice if somehow 50 collisions occur
    return f"ORG-{uuid.uuid4().hex[:8].upper()}"


async def get(db: AsyncSession, org_id: uuid.UUID) -> Organization | None:
    return await db.get(Organization, org_id)


async def get_by_code(db: AsyncSession, org_code: str) -> Organization | None:
    clean = org_code.strip().upper()
    return await db.scalar(select(Organization).where(func.upper(Organization.org_code) == clean))


async def create(
    db: AsyncSession,
    name: str,
    location: str | None = None,
    currency: str = "USD",
) -> Organization:
    org_code = await generate_unique_org_code(db)
    org = Organization(
        org_code=org_code,
        name=name.strip(),
        location=location.strip() if location else None,
        currency=currency.upper(),
    )
    db.add(org)
    await db.flush()
    await db.refresh(org)
    return org


async def update(db: AsyncSession, org: Organization, data: OrganizationUpdate) -> Organization:
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in changes.items():
        if field == "currency" and value:
            setattr(org, field, value.upper())
        elif field == "name" and value:
            setattr(org, field, value.strip())
        elif field == "location":
            setattr(org, field, value.strip() if value else None)
        else:
            setattr(org, field, value)
    await save(db, org)
    return org
