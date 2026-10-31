"""NAP-083: app_versions.auto_release — publish right after approval when a price is set.

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_versions", sa.Column("auto_release", sa.Boolean(), server_default="true", nullable=False))


def downgrade() -> None:
    op.drop_column("app_versions", "auto_release")
