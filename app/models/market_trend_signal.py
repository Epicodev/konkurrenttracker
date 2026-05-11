from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class MarketTrendSignal(SQLModel, table=True):
    """Markedssignal genereret af Sonnet ud fra MarketJobPosting time-series.

    Eksempler: 'Cloud-security efterspørgsel +40% over 3 mdr', 'Første AI Safety
    Engineer-rolle i DK', 'FinOps-roller eksploderede denne måned'.

    Bruges til at advare Epico om kompetencer der trender FØR konkurrenter har
    reageret - prediktiv vs. reaktiv signalering.
    """

    __tablename__ = "market_trend_signals"

    id: int | None = Field(default=None, primary_key=True)
    week: str = Field(max_length=10, index=True)
    signal_type: str = Field(max_length=50, index=True)  # growth | emerging | decline | spike
    specialization: str | None = Field(default=None, max_length=100, index=True)
    tech: str | None = Field(default=None, max_length=100, index=True)
    severity: str = Field(default="signal", max_length=20)  # urgent | signal | opportunity
    title: str = Field(max_length=500)
    summary: str
    delta_pct: float | None = None  # fx 0.40 = +40%
    sample_size: int | None = None  # antal jobs der bakker signalet op
    recommended_action: str | None = None
    confidence: str = Field(default="medium", max_length=20)
    source_refs: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
