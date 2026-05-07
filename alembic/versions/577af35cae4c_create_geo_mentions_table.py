"""create geo_mentions table

Revision ID: 577af35cae4c
Revises: 68cbbc3bdb68
Create Date: 2026-05-07 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "577af35cae4c"
down_revision: Union[str, Sequence[str], None] = "3b97768f40b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "geo_mentions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("week", sqlmodel.sql.sqltypes.AutoString(length=10), nullable=False),
        sa.Column("competitor_id", sa.Integer(), nullable=False),
        sa.Column("ai_engine", sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False),
        sa.Column("mentions", sa.Integer(), nullable=False),
        sa.Column("total_queries", sa.Integer(), nullable=False),
        sa.Column("share_of_voice", sa.Float(), nullable=False),
        sa.Column("sentiment", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("sample_quotes", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["competitor_id"], ["competitors.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_geo_mentions_week"), "geo_mentions", ["week"], unique=False)
    op.create_index(
        op.f("ix_geo_mentions_competitor_id"), "geo_mentions", ["competitor_id"], unique=False
    )
    op.create_index(
        op.f("ix_geo_mentions_ai_engine"), "geo_mentions", ["ai_engine"], unique=False
    )
    op.create_index(
        op.f("ix_geo_mentions_created_at"), "geo_mentions", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_geo_mentions_created_at"), table_name="geo_mentions")
    op.drop_index(op.f("ix_geo_mentions_ai_engine"), table_name="geo_mentions")
    op.drop_index(op.f("ix_geo_mentions_competitor_id"), table_name="geo_mentions")
    op.drop_index(op.f("ix_geo_mentions_week"), table_name="geo_mentions")
    op.drop_table("geo_mentions")
