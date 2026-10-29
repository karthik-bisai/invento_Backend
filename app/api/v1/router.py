from fastapi import APIRouter

from app.api.v1.endpoints import (
    access_requests,
    auth,
    health,
    internal,
    inventory,
    notifications,
    organizations,
    products,
    roles,
    warehouses,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(organizations.router)
api_router.include_router(access_requests.router)
api_router.include_router(notifications.router)
api_router.include_router(internal.router)
api_router.include_router(products.router)
api_router.include_router(warehouses.router)
api_router.include_router(inventory.router)
api_router.include_router(roles.router)
