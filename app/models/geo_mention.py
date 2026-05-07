from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class GeoMention(SQLModel, table=True):
    __tablename__ = "geo_mentions"

    id: int | None = Field(default=None, primary_key=True)
    week: str = Field(max_length=10, index=True)
    competitor_id: int = Field(foreign_key="competitors.id", index=True)
    ai_engine: str = Field(max_length=30, index=True)  # claude | openai | perplexity
    mentions: int = Field(default=0)
    total_queries: int = Field(default=0)
    share_of_voice: float = Field(default=0.0)  # mentions / total_queries
    sentiment: str = Field(default="neutral", max_length=20)  # positive | neutral | negative | mixed
    sample_quotes: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
