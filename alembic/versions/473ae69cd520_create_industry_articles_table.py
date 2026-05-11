"""create industry_articles table

Revision ID: 473ae69cd520
Revises: 8fe1eed5ff14
Create Date: 2026-05-11 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "473ae69cd520"
down_revision: Union[str, Sequence[str], None] = "8fe1eed5ff14"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "industry_articles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
        sa.Column("external_id", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(length=1000), nullable=True),
        sa.Column("raw_data", sa.JSON(), nullable=True),
        sa.Column("topic", sqlmodel.sql.sqltypes.AutoString(length=50), nullable=True),
        sa.Column("geo_scope", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=True),
        sa.Column("mentioned_competitors", sa.JSON(), nullable=True),
        sa.Column("is_classified", sa.Boolean(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("classified_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "external_id"),
    )
    op.create_index(op.f("ix_industry_articles_source"), "industry_articles", ["source"], unique=False)
    op.create_index(op.f("ix_industry_articles_topic"), "industry_articles", ["topic"], unique=False)
    op.create_index(op.f("ix_industry_articles_geo_scope"), "industry_articles", ["geo_scope"], unique=False)
    op.create_index(op.f("ix_industry_articles_is_classified"), "industry_articles", ["is_classified"], unique=False)
    op.create_index(op.f("ix_industry_articles_first_seen_at"), "industry_articles", ["first_seen_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_industry_articles_first_seen_at"), table_name="industry_articles")
    op.drop_index(op.f("ix_industry_articles_is_classified"), table_name="industry_articles")
    op.drop_index(op.f("ix_industry_articles_geo_scope"), table_name="industry_articles")
    op.drop_index(op.f("ix_industry_articles_topic"), table_name="industry_articles")
    op.drop_index(op.f("ix_industry_articles_source"), table_name="industry_articles")
    op.drop_table("industry_articles")
