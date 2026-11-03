"""NAP-112: a studio export can be sent to an app automatically when it finishes ("for all equipment")

Revision ID: 0017
Revises: 0016
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("render_jobs", sa.Column("deliver_app_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(op.f("fk_render_jobs_deliver_app_id_apps"), "render_jobs", "apps", ["deliver_app_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint(op.f("fk_render_jobs_deliver_app_id_apps"), "render_jobs", type_="foreignkey")
    op.drop_column("render_jobs", "deliver_app_id")
