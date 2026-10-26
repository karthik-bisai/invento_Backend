"""Create the initial admin user. Run from backend/: python -m app.scripts.seed"""
import asyncio

from app.core.config import settings
from app.core.database import SessionLocal
from app.crud import user as crud_user
from app.models.user import Role
from app.schemas.user import UserCreate


async def main() -> None:
    async with SessionLocal() as db:
        if await crud_user.get_by_email(db, settings.FIRST_ADMIN_EMAIL):
            print(f"Admin {settings.FIRST_ADMIN_EMAIL} already exists")
            return
        await crud_user.create(
            db,
            UserCreate(
                email=settings.FIRST_ADMIN_EMAIL,
                password=settings.FIRST_ADMIN_PASSWORD,
                role=Role.ADMIN,
            ),
        )
        print(f"Created admin {settings.FIRST_ADMIN_EMAIL}")


if __name__ == "__main__":
    asyncio.run(main())
