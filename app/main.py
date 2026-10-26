from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings


def docs_urls(environment: str, api_prefix: str) -> dict[str, str | None]:
    """Interactive API docs help developers and hand attackers a map, so they exist only outside production."""
    if environment == "production":
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": f"{api_prefix}/openapi.json"}


app = FastAPI(title=settings.APP_NAME, **docs_urls(settings.ENVIRONMENT, settings.API_V1_PREFIX))

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,  # http://localhost:5173
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)
