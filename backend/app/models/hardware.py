"""Hardware store: sellers, warehouses, products/SKUs, compatibility, BOM, inventory, carts, orders,
quotes, parcels, tracking and returns."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, created_at, uuid_pk
from .system import _in

ORDER_STATUSES = ("awaiting_quote", "awaiting_payment", "confirmed", "cancelled", "closed")
FULFILLMENT_STATUSES = ("unfulfilled", "processing", "partially_shipped", "shipped", "delivered", "exception")
PAYMENT_STATUSES = ("unpaid", "pending", "paid", "partially_refunded", "refunded", "failed")
PARCEL_STATUSES = ("packing", "label_created", "handed_over", "in_transit", "customs", "out_for_delivery",
                   "delivered", "exception", "returned_to_sender", "cancelled")
COMPAT_STATUSES = ("verified", "vendor_claimed", "unverified", "incompatible")


class Seller(Base):
    __tablename__ = "sellers"
    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(16))  # operator (platform's own sale) / third_party
    country: Mapped[str] = mapped_column(String(2))
    status: Mapped[str] = mapped_column(String(16), default="active")
    settlement_policy: Mapped[dict] = mapped_column(JSONB, default=dict)  # separate hardware commercial policy
    created_at: Mapped[datetime] = created_at()


class SellerMember(Base):
    __tablename__ = "seller_members"
    id: Mapped[uuid.UUID] = uuid_pk()
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sellers.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # seller / fulfillment
    __table_args__ = (UniqueConstraint("seller_id", "user_id", "role"),)


class Warehouse(Base):
    __tablename__ = "warehouses"
    id: Mapped[uuid.UUID] = uuid_pk()
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sellers.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    country: Mapped[str] = mapped_column(String(2))
    address: Mapped[dict] = mapped_column(JSONB)


class DeviceModel(Base):
    """NAP SYSTEM chair models/API/firmware known to the platform."""
    __tablename__ = "device_models"
    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    api_version: Mapped[str] = mapped_column(String(16))
    firmware: Mapped[str] = mapped_column(String(32))
    capabilities: Mapped[dict] = mapped_column(JSONB)
    documented: Mapped[bool] = mapped_column(Boolean, default=False)  # manufacturer protocol available?


class Product(Base):
    __tablename__ = "products"
    id: Mapped[uuid.UUID] = uuid_pk()
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sellers.id"), index=True)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id"))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    brand: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16), default="simple")  # simple / kit_component / kit_stock
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft / submitted / published / rejected
    warranty_months: Mapped[int] = mapped_column(Integer, default=12)
    assembly_included: Mapped[bool] = mapped_column(Boolean, default=False)
    made_to_order: Mapped[bool] = mapped_column(Boolean, default=False)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=0)  # production time if made to order
    prep_days: Mapped[int] = mapped_column(Integer, default=1)
    freight_mode: Mapped[str] = mapped_column(String(16), default="parcel")  # parcel / custom_quote
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"))
    destinations: Mapped[list[str]] = mapped_column(ARRAY(String(2)), default=list)
    specs: Mapped[dict] = mapped_column(JSONB, default=dict)  # language-neutral specs
    hue: Mapped[int] = mapped_column(Integer, default=30)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()
    skus: Mapped[list["Sku"]] = relationship(lazy="selectin", order_by="Sku.code")
    __table_args__ = (CheckConstraint("kind IN ('simple','kit_component','kit_stock')", name="kind"),)


class Sku(Base):
    __tablename__ = "skus"
    id: Mapped[uuid.UUID] = uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    variant: Mapped[dict] = mapped_column(JSONB, default=dict)
    price_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    weight_g: Mapped[int] = mapped_column(Integer)
    length_mm: Mapped[int] = mapped_column(Integer)
    width_mm: Mapped[int] = mapped_column(Integer)
    height_mm: Mapped[int] = mapped_column(Integer)
    package_count: Mapped[int] = mapped_column(Integer, default=1)
    origin_country: Mapped[str] = mapped_column(String(2))
    hs_code: Mapped[str | None] = mapped_column(String(16))  # only if confirmed by supplier
    customs_description: Mapped[str | None] = mapped_column(String(255))
    contains_battery: Mapped[str] = mapped_column(String(48), default="none")  # none / li_ion_contained...
    provides: Mapped[list[str]] = mapped_column(ARRAY(String(48)), default=list)  # capabilities provided
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (CheckConstraint("price_minor >= 0", name="price"),)


class Compatibility(Base):
    __tablename__ = "compatibility"
    id: Mapped[uuid.UUID] = uuid_pk()
    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id"), index=True)
    device_model_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device_models.id"))
    feature: Mapped[str] = mapped_column(String(48))  # binaural_path / vibroacoustic / power / connector...
    status: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(120))
    tested_config: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)  # connectors, voltage, plug, formats...
    __table_args__ = (CheckConstraint(_in("status", COMPAT_STATUSES), name="status"),)


class Bom(Base):
    __tablename__ = "boms"
    id: Mapped[uuid.UUID] = uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()
    items: Mapped[list["BomItem"]] = relationship(lazy="selectin", order_by="BomItem.position")
    __table_args__ = (UniqueConstraint("product_id", "version"),)


class BomItem(Base):
    __tablename__ = "bom_items"
    id: Mapped[uuid.UUID] = uuid_pk()
    bom_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("boms.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    role: Mapped[str] = mapped_column(String(48))  # chair / headphones / power / cable...
    alternative_sku_ids: Mapped[list[str]] = mapped_column(ARRAY(String(36)), default=list)
    __table_args__ = (CheckConstraint("quantity > 0", name="qty"),)


class Inventory(Base):
    __tablename__ = "inventory"
    id: Mapped[uuid.UUID] = uuid_pk()
    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id"))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"))
    on_hand: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    allocated: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("sku_id", "warehouse_id"),
                      CheckConstraint("on_hand >= 0 AND reserved >= 0 AND allocated >= 0", name="nonneg"),
                      CheckConstraint("on_hand - reserved - allocated >= 0", name="available"))


class Reservation(Base):
    __tablename__ = "reservations"
    id: Mapped[uuid.UUID] = uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hardware_orders.id"), index=True)
    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id"))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="reserved")  # reserved/allocated/released/shipped
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shipped_qty: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created_at()


class Cart(Base):
    __tablename__ = "carts"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), unique=True)
    anon_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    owned_sku_ids: Mapped[list[str]] = mapped_column(ARRAY(String(36)), default=list)  # "I already have"
    updated_at: Mapped[datetime] = created_at()
    items: Mapped[list["CartItem"]] = relationship(lazy="selectin", cascade="all, delete-orphan")


class CartItem(Base):
    __tablename__ = "cart_items"
    id: Mapped[uuid.UUID] = uuid_pk()
    cart_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("carts.id"), index=True)
    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    source_bom_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("boms.id"))
    __table_args__ = (UniqueConstraint("cart_id", "sku_id"), CheckConstraint("quantity > 0", name="qty"))


class Address(Base):
    __tablename__ = "addresses"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    country: Mapped[str] = mapped_column(String(2))
    fields: Mapped[dict] = mapped_column(JSONB)  # unicode: name, line1, line2, city, region, postal_code, phone
    created_at: Mapped[datetime] = created_at()


class HardwareOrder(Base):
    __tablename__ = "hardware_orders"
    id: Mapped[uuid.UUID] = uuid_pk()
    number: Mapped[str] = mapped_column(String(20), unique=True)
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sellers.id"), index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20), default="awaiting_payment")
    payment_status: Mapped[str] = mapped_column(String(20), default="unpaid")
    fulfillment_status: Mapped[str] = mapped_column(String(20), default="unfulfilled")
    locale: Mapped[str] = mapped_column(String(5))
    address: Mapped[dict | None] = mapped_column(JSONB)  # snapshot
    terms: Mapped[dict] = mapped_column(JSONB, default=dict)  # incoterms / duties payer / return terms snapshot
    bom_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    shipping_rate: Mapped[dict | None] = mapped_column(JSONB)  # snapshot of accepted rate/quote
    goods_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    shipping_minor: Mapped[int | None] = mapped_column(BigInteger)  # NULL = unknown, never 0 by default
    fees_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    total_minor: Mapped[int | None] = mapped_column(BigInteger)
    refunded_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    composition_hash: Mapped[str] = mapped_column(String(64))
    group_ref: Mapped[str | None] = mapped_column(String(40), index=True)  # orders split from one cart
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exception_reason: Mapped[str | None] = mapped_column(String(64))
    items: Mapped[list["HardwareOrderItem"]] = relationship(lazy="selectin", order_by="HardwareOrderItem.position")
    __table_args__ = (CheckConstraint(_in("status", ORDER_STATUSES), name="status"),
                      CheckConstraint(_in("fulfillment_status", FULFILLMENT_STATUSES), name="fstatus"),
                      CheckConstraint(_in("payment_status", PAYMENT_STATUSES), name="pstatus"),
                      CheckConstraint("refunded_minor >= 0 AND (total_minor IS NULL OR refunded_minor <= total_minor)",
                                      name="refund_bound"))


class HardwareOrderItem(Base):
    __tablename__ = "hardware_order_items"
    id: Mapped[uuid.UUID] = uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hardware_orders.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger)
    snapshot: Mapped[dict] = mapped_column(JSONB)  # sku code, names ru/en, weight, dims, origin, hs, battery
    shipped_qty: Mapped[int] = mapped_column(Integer, default=0)
    returned_qty: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (CheckConstraint("quantity > 0", name="qty"),
                      CheckConstraint("shipped_qty >= 0 AND shipped_qty <= quantity", name="shipped_bound"),
                      CheckConstraint("returned_qty >= 0 AND returned_qty <= shipped_qty", name="returned_bound"))


class ShippingRate(Base):
    __tablename__ = "shipping_rates"
    id: Mapped[uuid.UUID] = uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hardware_orders.id"), index=True)
    adapter: Mapped[str] = mapped_column(String(16))
    service_code: Mapped[str] = mapped_column(String(64))
    carrier: Mapped[str] = mapped_column(String(64))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    eta_min_days: Mapped[int] = mapped_column(Integer)
    eta_max_days: Mapped[int] = mapped_column(Integer)
    packages: Mapped[list] = mapped_column(JSONB)
    insurance_minor: Mapped[int | None] = mapped_column(BigInteger)
    duties_payer: Mapped[str] = mapped_column(String(16), default="recipient")  # DAP default
    external_ref: Mapped[str | None] = mapped_column(String(128))
    composition_hash: Mapped[str] = mapped_column(String(140))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class FreightQuote(Base):
    """Custom quote: request -> supplier calculation -> price/validity -> buyer acceptance -> payment."""
    __tablename__ = "freight_quotes"
    id: Mapped[uuid.UUID] = uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hardware_orders.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="requested")  # requested/quoted/accepted/expired/declined
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(3))
    eta_min_days: Mapped[int | None] = mapped_column(Integer)
    eta_max_days: Mapped[int | None] = mapped_column(Integer)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str] = mapped_column(Text, default="")
    composition_hash: Mapped[str] = mapped_column(String(140))
    quoted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()


class Parcel(Base):
    __tablename__ = "parcels"
    id: Mapped[uuid.UUID] = uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hardware_orders.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    weight_g: Mapped[int] = mapped_column(Integer)
    length_mm: Mapped[int] = mapped_column(Integer)
    width_mm: Mapped[int] = mapped_column(Integer)
    height_mm: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="packing")
    status_source: Mapped[str | None] = mapped_column(String(16))  # carrier / supplier / simulator
    adapter: Mapped[str | None] = mapped_column(String(16))
    carrier: Mapped[str | None] = mapped_column(String(64))
    tracking_number: Mapped[str | None] = mapped_column(String(64))
    external_shipment_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    label_idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    label_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id"))
    documents: Mapped[list] = mapped_column(JSONB, default=list)  # [{kind, asset_id}]
    created_at: Mapped[datetime] = created_at()
    items: Mapped[list["ParcelItem"]] = relationship(lazy="selectin")
    __table_args__ = (UniqueConstraint("order_id", "number"), CheckConstraint(_in("status", PARCEL_STATUSES),
                                                                              name="status"))


class ParcelItem(Base):
    __tablename__ = "parcel_items"
    id: Mapped[uuid.UUID] = uuid_pk()
    parcel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parcels.id"), index=True)
    order_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hardware_order_items.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    __table_args__ = (CheckConstraint("quantity > 0", name="qty"), UniqueConstraint("parcel_id", "order_item_id"))


class TrackingEvent(Base):
    __tablename__ = "tracking_events"
    id: Mapped[uuid.UUID] = uuid_pk()
    parcel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parcels.id"), index=True)
    source: Mapped[str] = mapped_column(String(16))  # carrier / supplier / simulator
    external_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24))
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str | None] = mapped_column(String(160))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = created_at()
    __table_args__ = (UniqueConstraint("parcel_id", "source", "external_id"),)


class ReturnRequest(Base):
    __tablename__ = "return_requests"
    id: Mapped[uuid.UUID] = uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hardware_orders.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="requested")
    # requested -> approved/rejected -> in_transit -> received -> inspected -> closed
    items: Mapped[list] = mapped_column(JSONB)  # [{order_item_id, quantity}]
    reason: Mapped[str] = mapped_column(Text)
    decision_note: Mapped[str] = mapped_column(Text, default="")
    return_tracking: Mapped[str | None] = mapped_column(String(64))
    inspection: Mapped[dict | None] = mapped_column(JSONB)  # {order_item_id: {accepted, restock}}
    restocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
