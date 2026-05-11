from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


class MarketJobPosting(SQLModel, table=True):
    """Alle IT-jobopslag i DK-markedet - uafhængigt af konkurrent-liste.

    Bruges til trend-detektion på markedet (hvilke skills er i fremgang,
    nye rolletyper, m.m.) - ikke til pr.-konkurrent overvågning.
    """

    __tablename__ = "market_job_postings"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(max_length=50, index=True)  # jobindex_it | it_jobbank
    external_id: str = Field(max_length=500)
    title: str = Field(max_length=500)
    description: str | None = None
    url: str | None = Field(default=None, max_length=1000)
    company: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    raw_data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # Klassificering (Haiku) - alle valgfri indtil klassificeret
    category: str | None = Field(default=None, max_length=100, index=True)
    seniority: str | None = Field(default=None, max_length=50, index=True)
    is_freelance: bool | None = None
    tech_stack: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    specialization: str | None = Field(default=None, max_length=100, index=True)
    is_emerging: bool = Field(default=False)

    first_seen_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    classified_at: datetime | None = None
