"""NAP-092: conversion jobs remember the stage they failed at (status timeline for authors)

Revision ID: 0014
Revises: 0013
"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversion_jobs", sa.Column("failed_stage", sa.String(length=16), nullable=True))   # backward compatible


def downgrade() -> None:
    op.drop_column("conversion_jobs", "failed_stage")
