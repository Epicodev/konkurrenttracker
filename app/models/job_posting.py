from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


class JobPosting(SQLModel, table=True):
    __tablename__ = "job_postings"
    __table_args__ = (UniqueConstraint("competitor_id", "external_id"),)

    id: int | None = Field(default=None, primary_key=True)
    competitor_id: int = Field(foreign_key="competitors.id", index=True)
    external_id: str = Field(max_length=500, index=True)
    title: str = Field(max_length=500)
    description: str | None = None
    location: str | None = Field(default=None, max_length=200)
    source: str = Field(max_length=50, index=True)
    url: str | None = Field(default=None, max_length=1000)
    category: str | None = Field(default=None, max_length=100)
    seniority: str | None = Field(default=None, max_length=50)
    is_freelance: bool | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: datetime | None = None
