from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


class CompanyEvent(SQLModel, table=True):
    __tablename__ = "company_events"
    __table_args__ = (UniqueConstraint("competitor_id", "source", "external_id"),)

    id: int | None = Field(default=None, primary_key=True)
    competitor_id: int = Field(foreign_key="competitors.id", index=True)
    event_type: str = Field(max_length=50, index=True)  # cvr_baseline | cvr_change | news | web_change
    source: str = Field(max_length=50, index=True)  # cvr | google_news | wayback
    external_id: str | None = Field(default=None, max_length=500)  # natural key (fx URL for news)
    title: str = Field(max_length=500)
    description: str | None = None
    url: str | None = Field(default=None, max_length=1000)
    raw_data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    occurred_at: datetime | None = None
    detected_at: datetime = Field(default_factory=datetime.utcnow, index=True)
