"""NAP-117: how a conversion makes the beat — from the track itself (whole-track shift), the shift plus the carrier
layer, or the carrier layer only (the method of every earlier job)

Revision ID: 0018
Revises: 0017
"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # existing jobs were made with the carrier layer: the default keeps their meaning
    op.add_column("conversion_jobs", sa.Column("method", sa.String(length=16), nullable=False, server_default="layer"))
    op.create_check_constraint(op.f("ck_conversion_jobs_method"), "conversion_jobs", "method IN ('layer', 'shift', 'shift_layer')")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_conversion_jobs_method"), "conversion_jobs", type_="check")
    op.drop_column("conversion_jobs", "method")
