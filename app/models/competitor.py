from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class Competitor(SQLModel, table=True):
    __tablename__ = "competitors"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(max_length=50, unique=True, index=True)
    name: str = Field(max_length=200)
    cvr: str | None = Field(default=None, max_length=20)
    domain: str | None = Field(default=None, max_length=200)
    career_url: str | None = Field(default=None, max_length=500)
    scraper_config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
