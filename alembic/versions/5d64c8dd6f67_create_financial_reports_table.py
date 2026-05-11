"""create financial_reports table

Revision ID: 5d64c8dd6f67
Revises: 577af35cae4c
Create Date: 2026-05-07 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "5d64c8dd6f67"
down_revision: Union[str, Sequence[str], None] = "577af35cae4c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "financial_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("competitor_id", sa.Integer(), nullable=False),
        sa.Column("fiscal_year_start", sa.Date(), nullable=True),
        sa.Column("fiscal_year_end", sa.Date(), nullable=False),
        sa.Column("revenue", sa.Float(), nullable=True),
        sa.Column("gross_profit", sa.Float(), nullable=True),
        sa.Column("profit_loss", sa.Float(), nullable=True),
        sa.Column("employee_expenses", sa.Float(), nullable=True),
        sa.Column("equity", sa.Float(), nullable=True),
        sa.Column("assets", sa.Float(), nullable=True),
        sa.Column("average_employees", sa.Integer(), nullable=True),
        sa.Column("pdf_url", sqlmodel.sql.sqltypes.AutoString(length=1000), nullable=True),
        sa.Column("xbrl_url", sqlmodel.sql.sqltypes.AutoString(length=1000), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["competitor_id"], ["competitors.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("competitor_id", "fiscal_year_end"),
    )
    op.create_index(
        op.f("ix_financial_reports_competitor_id"),
        "financial_reports",
        ["competitor_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_financial_reports_fiscal_year_end"),
        "financial_reports",
        ["fiscal_year_end"],
        unique=False,
    )
    op.create_index(
        op.f("ix_financial_reports_fetched_at"),
        "financial_reports",
        ["fetched_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_financial_reports_fetched_at"), table_name="financial_reports")
    op.drop_index(op.f("ix_financial_reports_fiscal_year_end"), table_name="financial_reports")
    op.drop_index(op.f("ix_financial_reports_competitor_id"), table_name="financial_reports")
    op.drop_table("financial_reports")
