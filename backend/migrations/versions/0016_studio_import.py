"""NAP-110: a studio export becomes an app track — such conversion jobs have no DSP profile (the mix is final)

Revision ID: 0016
Revises: 0015
"""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("conversion_jobs", "profile_id", existing_type=sa.dialects.postgresql.UUID(), nullable=True)


def downgrade() -> None:
    # only possible while no studio imports exist
    op.alter_column("conversion_jobs", "profile_id", existing_type=sa.dialects.postgresql.UUID(), nullable=False)
