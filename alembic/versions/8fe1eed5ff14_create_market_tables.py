"""create market_job_postings + market_trend_signals tables

Revision ID: 8fe1eed5ff14
Revises: 5d64c8dd6f67
Create Date: 2026-05-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "8fe1eed5ff14"
down_revision: Union[str, Sequence[str], None] = "5d64c8dd6f67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_job_postings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
        sa.Column("external_id", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(length=1000), nullable=True),
        sa.Column("company", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=True),
        sa.Column("location", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=True),
        sa.Column("raw_data", sa.JSON(), nullable=True),
        sa.Column("category", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True),
        sa.Column("seniority", sqlmodel.sql.sqltypes.AutoString(length=50), nullable=True),
        sa.Column("is_freelance", sa.Boolean(), nullable=True),
        sa.Column("tech_stack", sa.JSON(), nullable=True),
        sa.Column("specialization", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True),
        sa.Column("is_emerging", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("classified_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "external_id"),
    )
    op.create_index(op.f("ix_market_job_postings_source"), "market_job_postings", ["source"], unique=False)
    op.create_index(op.f("ix_market_job_postings_category"), "market_job_postings", ["category"], unique=False)
    op.create_index(op.f("ix_market_job_postings_seniority"), "market_job_postings", ["seniority"], unique=False)
    op.create_index(op.f("ix_market_job_postings_specialization"), "market_job_postings", ["specialization"], unique=False)
    op.create_index(op.f("ix_market_job_postings_first_seen_at"), "market_job_postings", ["first_seen_at"], unique=False)

    op.create_table(
        "market_trend_signals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("week", sqlmodel.sql.sqltypes.AutoString(length=10), nullable=False),
        sa.Column("signal_type", sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
        sa.Column("specialization", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True),
        sa.Column("tech", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True),
        sa.Column("severity", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("delta_pct", sa.Float(), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=True),
        sa.Column("recommended_action", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("confidence", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("source_refs", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_market_trend_signals_week"), "market_trend_signals", ["week"], unique=False)
    op.create_index(op.f("ix_market_trend_signals_signal_type"), "market_trend_signals", ["signal_type"], unique=False)
    op.create_index(op.f("ix_market_trend_signals_specialization"), "market_trend_signals", ["specialization"], unique=False)
    op.create_index(op.f("ix_market_trend_signals_tech"), "market_trend_signals", ["tech"], unique=False)
    op.create_index(op.f("ix_market_trend_signals_created_at"), "market_trend_signals", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_market_trend_signals_created_at"), table_name="market_trend_signals")
    op.drop_index(op.f("ix_market_trend_signals_tech"), table_name="market_trend_signals")
    op.drop_index(op.f("ix_market_trend_signals_specialization"), table_name="market_trend_signals")
    op.drop_index(op.f("ix_market_trend_signals_signal_type"), table_name="market_trend_signals")
    op.drop_index(op.f("ix_market_trend_signals_week"), table_name="market_trend_signals")
    op.drop_table("market_trend_signals")
    op.drop_index(op.f("ix_market_job_postings_first_seen_at"), table_name="market_job_postings")
    op.drop_index(op.f("ix_market_job_postings_specialization"), table_name="market_job_postings")
    op.drop_index(op.f("ix_market_job_postings_seniority"), table_name="market_job_postings")
    op.drop_index(op.f("ix_market_job_postings_category"), table_name="market_job_postings")
    op.drop_index(op.f("ix_market_job_postings_source"), table_name="market_job_postings")
    op.drop_table("market_job_postings")
