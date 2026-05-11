from datetime import date, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class FinancialReport(SQLModel, table=True):
    __tablename__ = "financial_reports"
    __table_args__ = (UniqueConstraint("competitor_id", "fiscal_year_end"),)

    id: int | None = Field(default=None, primary_key=True)
    competitor_id: int = Field(foreign_key="competitors.id", index=True)
    # Regnskabsperiode
    fiscal_year_start: date | None = None
    fiscal_year_end: date = Field(index=True)
    # Noegletal i kroner (DKK) - alle kan vaere None hvis ikke rapporteret
    revenue: float | None = None  # Omsaetning (Revenue)
    gross_profit: float | None = None  # Bruttoresultat (GrossProfitLoss)
    profit_loss: float | None = None  # Aarets resultat (ProfitLoss)
    employee_expenses: float | None = None  # Loenudgifter (EmployeeBenefitsExpense)
    equity: float | None = None  # Egenkapital (Equity)
    assets: float | None = None  # Balancesum (Assets)
    average_employees: int | None = None  # Gennemsnitligt antal ansatte
    # Metadata
    pdf_url: str | None = Field(default=None, max_length=1000)
    xbrl_url: str | None = Field(default=None, max_length=1000)
    published_at: datetime | None = None  # offentliggoerelsesTidspunkt
    fetched_at: datetime = Field(default_factory=datetime.utcnow, index=True)
