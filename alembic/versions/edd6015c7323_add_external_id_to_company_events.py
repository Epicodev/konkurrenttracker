"""add external_id to company_events

Revision ID: edd6015c7323
Revises: b8edb6a3cf05
Create Date: 2026-04-29 10:20:36.577618

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'edd6015c7323'
down_revision: Union[str, Sequence[str], None] = 'b8edb6a3cf05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # batch_alter_table er noedvendigt for SQLite (lokal dev) - PostgreSQL bruger ALTER direkte.
    with op.batch_alter_table('company_events') as batch_op:
        batch_op.add_column(
            sa.Column('external_id', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True)
        )
        batch_op.create_unique_constraint(
            'uq_company_events_competitor_source_external',
            ['competitor_id', 'source', 'external_id'],
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('company_events') as batch_op:
        batch_op.drop_constraint('uq_company_events_competitor_source_external', type_='unique')
        batch_op.drop_column('external_id')
