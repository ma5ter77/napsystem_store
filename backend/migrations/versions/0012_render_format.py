"""audio studio M2: export format per render job (wav | flac); WAV above 4 GiB is written as RF64

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # backward compatible: a nullable-free column with a server default, older code simply ignores it
    op.add_column("render_jobs", sa.Column("format", sa.String(length=8), nullable=False, server_default="wav"))
    op.create_check_constraint(op.f("ck_render_jobs_format"), "render_jobs", "format IN ('wav','flac')")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_render_jobs_format"), "render_jobs", type_="check")
    op.drop_column("render_jobs", "format")
