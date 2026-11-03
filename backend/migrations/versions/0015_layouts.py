"""NAP-108: conversion jobs for equipment layouts (3.0 / 5.1 / 7.1) with a file per channel; program sessions keep
a version per layout next to the headphones track

Revision ID: 0015
Revises: 0014
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

LAYOUTS = ("headphones", "acoustic_3_0", "surround_5_1", "surround_7_1")


def upgrade() -> None:
    # backward compatible: existing jobs are headphones jobs, existing sessions have no extra layouts
    op.add_column("conversion_jobs", sa.Column("layout", sa.String(length=16), nullable=False, server_default="headphones"))
    op.add_column("conversion_jobs", sa.Column("stems", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.create_check_constraint(op.f("ck_conversion_jobs_layout"), "conversion_jobs",
                               "layout IN (" + ", ".join(f"'{x}'" for x in LAYOUTS) + ")")
    op.add_column("program_sessions", sa.Column("variants", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column("program_sessions", "variants")
    op.drop_constraint(op.f("ck_conversion_jobs_layout"), "conversion_jobs", type_="check")
    op.drop_column("conversion_jobs", "stems")
    op.drop_column("conversion_jobs", "layout")
