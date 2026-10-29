import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SUPPORTED_CURRENCIES = ["USD", "INR", "EUR", "GBP", "AED", "SAR", "CAD", "AUD"]
CurrencyCode = Literal["USD", "INR", "EUR", "GBP", "AED", "SAR", "CAD", "AUD"]


class OrganizationBase(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    location: str | None = Field(default=None, max_length=255)
    currency: CurrencyCode = "USD"


class OrganizationCreate(OrganizationBase):
    pass


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    location: str | None = Field(default=None, max_length=255)
    currency: CurrencyCode | None = None


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_code: str
    name: str
    location: str | None = None
    currency: str = "USD"
    created_at: datetime


class OrganizationLookup(BaseModel):
    exists: bool
    name: str | None = None
    org_code: str | None = None
