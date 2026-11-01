"""product photos (NAP-047)

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-01 07:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = ("source", "master", "delivery", "preview", "public_preview", "qa_report", "params", "layer",
       "cover", "document", "label")
NEW = OLD + ("product_photo",)


def _kinds(kinds) -> str:
    return "kind IN (" + ", ".join(f"'{k}'" for k in kinds) + ")"


def upgrade() -> None:
    op.add_column('products', sa.Column('photos', postgresql.JSONB(astext_type=sa.Text()), server_default='[]',
                                        nullable=False))
    op.drop_constraint(op.f('ck_assets_kind'), 'assets', type_='check')
    op.create_check_constraint(op.f('ck_assets_kind'), 'assets', _kinds(NEW))


def downgrade() -> None:
    op.execute("DELETE FROM assets WHERE kind = 'product_photo'")
    op.drop_constraint(op.f('ck_assets_kind'), 'assets', type_='check')
    op.create_check_constraint(op.f('ck_assets_kind'), 'assets', _kinds(OLD))
    op.drop_column('products', 'photos')
