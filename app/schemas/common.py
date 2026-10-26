from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")


class PageParams:
    def __init__(
        self,
        page: int = Query(1, ge=1),
        size: int = Query(20, ge=1, le=100),
    ) -> None:
        self.page = page
        self.size = size


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int
