"""NAP-116: what buyers see of a product as last approved, so a resubmitted card shows "before → after"

Revision ID: 0019
Revises: 0018
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

# the same shape as app.services.products.snapshot(); products on sale now are their own approved state
BACKFILL = """
UPDATE products p SET approved_at = now(), approved_snapshot = jsonb_build_object(
  'texts', COALESCE((SELECT jsonb_object_agg(t.locale, t.data) FROM translations t
                     WHERE t.entity_type = 'product' AND t.entity_id = p.id), '{}'::jsonb),
  'fields', jsonb_build_object('category', (SELECT c.slug FROM categories c WHERE c.id = p.category_id),
      'warranty_months', p.warranty_months, 'assembly_included', p.assembly_included,
      'made_to_order', p.made_to_order, 'lead_time_days', p.lead_time_days, 'prep_days', p.prep_days,
      'freight_mode', p.freight_mode, 'destinations', to_jsonb(COALESCE(p.destinations, ARRAY[]::varchar[]))),
  'specs', COALESCE(p.specs, '{}'::jsonb),
  'skus', COALESCE((SELECT jsonb_object_agg(s.code, jsonb_build_object(
      'variant', s.variant, 'price_minor', s.price_minor, 'currency', s.currency, 'weight_g', s.weight_g,
      'length_mm', s.length_mm, 'width_mm', s.width_mm, 'height_mm', s.height_mm,
      'package_count', s.package_count, 'origin_country', s.origin_country, 'hs_code', s.hs_code,
      'contains_battery', s.contains_battery, 'active', s.active)) FROM skus s WHERE s.product_id = p.id), '{}'::jsonb),
  'photos', COALESCE(p.photos, '[]'::jsonb))
WHERE p.status = 'published'
"""


def upgrade() -> None:
    op.add_column("products", sa.Column("approved_snapshot", JSONB(), nullable=True))
    op.add_column("products", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(BACKFILL)


def downgrade() -> None:
    op.drop_column("products", "approved_at")
    op.drop_column("products", "approved_snapshot")
