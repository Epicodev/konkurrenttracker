from datetime import datetime

from sqlmodel import Field, SQLModel


class Report(SQLModel, table=True):
    __tablename__ = "reports"

    id: int | None = Field(default=None, primary_key=True)
    week: str = Field(max_length=10, unique=True, index=True)  # "2026-W17"
    pdf_path: str | None = Field(default=None, max_length=500)
    signal_count: int = 0
    data_points: int = 0
    exec_summary: str | None = None
    status: str = Field(default="pending", max_length=20)  # pending | sent | failed
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    sent_at: datetime | None = None
