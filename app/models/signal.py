from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class Signal(SQLModel, table=True):
    __tablename__ = "signals"

    id: int | None = Field(default=None, primary_key=True)
    week: str = Field(max_length=10, index=True)  # "2026-W17"
    competitor_id: int = Field(foreign_key="competitors.id", index=True)
    domain: str = Field(max_length=50)  # jobs | company | web
    severity: str = Field(max_length=20, index=True)  # urgent | signal | opportunity
    title: str = Field(max_length=500)
    summary: str
    recommended_action: str | None = None
    recommended_owner: str | None = Field(default=None, max_length=100)
    confidence: str = Field(default="medium", max_length=20)  # low | medium | high
    source_refs: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
