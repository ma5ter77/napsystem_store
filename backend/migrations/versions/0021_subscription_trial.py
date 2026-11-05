"""NAP-064: free trial before a subscription — the trial the buyer got (once per app)

Revision ID: 0021
Revises: 0020
"""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("trial_days", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("subscriptions", sa.Column("trial_end", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("subscriptions", "trial_end")
    op.drop_column("subscriptions", "trial_days")
