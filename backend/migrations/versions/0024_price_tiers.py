"""NAP-064: subscription tiers — each tier has its own price and set of program sessions

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-06 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0024'
down_revision: Union[str, Sequence[str], None] = '0023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('prices', sa.Column('tier', sa.String(length=32), server_default='', nullable=False))
    op.add_column('prices', sa.Column('tier_names', postgresql.JSONB(astext_type=sa.Text()), server_default='{}',
                                      nullable=False))
    op.add_column('prices', sa.Column('sessions', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_check_constraint(op.f('ck_prices_tier'), 'prices', "kind = 'subscription' OR (tier = '' AND sessions IS NULL)")


def downgrade() -> None:
    op.drop_constraint(op.f('ck_prices_tier'), 'prices', type_='check')
    op.drop_column('prices', 'sessions')
    op.drop_column('prices', 'tier_names')
    op.drop_column('prices', 'tier')
