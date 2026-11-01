"""Stripe for the US and world markets (NAP-010/NAP-012/NAP-020): a reusable payment customer per user
and tax on hardware orders.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-01 19:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('payment_customer_ref', sa.String(length=64), nullable=True))
    op.add_column('hardware_orders', sa.Column('tax_minor', sa.BigInteger(), server_default='0', nullable=False))
    op.create_check_constraint(op.f('ck_hardware_orders_tax'), 'hardware_orders', 'tax_minor >= 0')


def downgrade() -> None:
    op.drop_constraint(op.f('ck_hardware_orders_tax'), 'hardware_orders', type_='check')
    op.drop_column('hardware_orders', 'tax_minor')
    op.drop_column('users', 'payment_customer_ref')
