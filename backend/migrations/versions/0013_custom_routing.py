"""audio studio M2: custom routing profiles (owner, per-channel options: mute, polarity, delay; archive flag)

Revision ID: 0013
Revises: 0012
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # backward compatible: new nullable / defaulted columns, built-in rows stay as they are
    op.add_column("routing_profiles", sa.Column("owner_id", sa.UUID(), nullable=True))
    op.add_column("routing_profiles", sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                                                server_default="[]"))
    op.add_column("routing_profiles", sa.Column("archived", sa.Boolean(), nullable=False, server_default="false"))
    op.create_index(op.f("ix_routing_profiles_owner_id"), "routing_profiles", ["owner_id"])
    op.create_foreign_key(op.f("fk_routing_profiles_owner_id_users"), "routing_profiles", "users", ["owner_id"], ["id"])


def downgrade() -> None:
    op.execute("DELETE FROM routing_profiles WHERE builtin = false")
    op.drop_constraint(op.f("fk_routing_profiles_owner_id_users"), "routing_profiles", type_="foreignkey")
    op.drop_index(op.f("ix_routing_profiles_owner_id"), table_name="routing_profiles")
    op.drop_column("routing_profiles", "archived")
    op.drop_column("routing_profiles", "options")
    op.drop_column("routing_profiles", "owner_id")
