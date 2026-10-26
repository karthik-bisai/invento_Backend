from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "Invento"
    ENVIRONMENT: str = "development"
    API_V1_PREFIX: str = "/api/v1"

    DATABASE_URL: str = "postgresql+asyncpg://inventory:inventory_dev_password@localhost:5432/inventory"
    REDIS_URL: str = "redis://localhost:6379/0"

    SECRET_KEY: str = "change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    FIRST_ADMIN_EMAIL: str = "admin@example.com"
    FIRST_ADMIN_PASSWORD: str = "ChangeMe123!"

    # Self-registered accounts stay inactive until an admin approves them, unless this is true.
    SIGNUP_AUTO_ACTIVATE: bool = False

    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    # Shared secret the Node gateway presents on /internal/* calls. Never exposed to browsers.
    INTERNAL_API_KEY: str = "dev-internal-key"
    # Avatars may only point at our own media host (the gateway serves local files, or the S3 bucket's public URL).
    MEDIA_PUBLIC_URL: str = "http://localhost:4000/media/"

    # Brute-force protection on the unauthenticated endpoints (see app/api/rate_limit.py).
    RATE_LIMIT_ENABLED: bool = True
    LOGIN_ATTEMPTS_PER_MINUTE: int = 10  # per client address + email
    LOGIN_ATTEMPTS_PER_MINUTE_PER_IP: int = 30  # per client address, across emails
    REGISTRATIONS_PER_HOUR: int = 5  # per client address
    # Only enable behind a reverse proxy you control; otherwise clients can spoof X-Forwarded-For.
    TRUST_PROXY_HEADERS: bool = False

    @model_validator(mode="after")
    def _require_real_secrets_outside_development(self) -> "Settings":
        if self.ENVIRONMENT != "development":
            weak = {
                "SECRET_KEY": self.SECRET_KEY == "change-me",
                "FIRST_ADMIN_PASSWORD": self.FIRST_ADMIN_PASSWORD == "ChangeMe123!",
                "INTERNAL_API_KEY": self.INTERNAL_API_KEY == "dev-internal-key",
            }
            bad = [name for name, is_default in weak.items() if is_default]
            if bad:
                raise ValueError(f"Set {', '.join(bad)} to a non-default value when ENVIRONMENT={self.ENVIRONMENT!r}")
        return self


settings = Settings()
