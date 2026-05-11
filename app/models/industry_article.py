from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


class IndustryArticle(SQLModel, table=True):
    """Industri-presseartikler fra danske + internationale IT-medier.

    Adskilt fra konkurrent-specifik Google News (CompanyEvent) - dette er
    HELE branchefeedet for at fange marked-tematik og puls.
    """

    __tablename__ = "industry_articles"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(max_length=50, index=True)  # version2 | borsen | it_branchen | tech_eu | the_register | techcrunch | siliconangle | berlingske
    external_id: str = Field(max_length=500)
    title: str = Field(max_length=500)
    description: str | None = None
    url: str | None = Field(default=None, max_length=1000)
    raw_data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # Haiku-klassificering
    topic: str | None = Field(default=None, max_length=50, index=True)  # cloud | ai_ml | cybersecurity | m_a | funding | regulation | talent | new_tech | dk_market | other
    geo_scope: str | None = Field(default=None, max_length=20, index=True)  # dk | eu | global
    mentioned_competitors: list[Any] = Field(default_factory=list, sa_column=Column(JSON))  # liste af konkurrent-slugs
    is_classified: bool = Field(default=False, index=True)

    published_at: datetime | None = None
    first_seen_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    classified_at: datetime | None = None
