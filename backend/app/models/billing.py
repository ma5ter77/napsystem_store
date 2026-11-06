"""Digital commerce: prices, orders, payment attempts, subscriptions, entitlements, ledger, payouts.

All money is integer minor units; currency is always stored next to the amount and never mixed.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, created_at, uuid_pk


class Price(Base):
    __tablename__ = "prices"
    id: Mapped[uuid.UUID] = uuid_pk()
    app_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("apps.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # one_time / subscription / free
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    interval: Mapped[str | None] = mapped_column(String(8))  # month / year
    trial_days: Mapped[int] = mapped_column(Integer, default=0)
    # NAP-064: subscription tiers. "" is the whole app; a named tier opens only `sessions` (None = every session)
    tier: Mapped[str] = mapped_column(String(32), default="", server_default="")
    tier_names: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    sessions: Mapped[list | None] = mapped_column(JSONB(none_as_null=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (CheckConstraint("kind IN ('one_time','subscription','free')", name="kind"),
                      CheckConstraint("kind = 'subscription' OR (tier = '' AND sessions IS NULL)", name="tier"),
                      CheckConstraint("amount_minor >= 0", name="amount"),
                      CheckConstraint("(kind = 'subscription') = (interval IS NOT NULL)", name="interval"))


class DigitalOrder(Base):
    __tablename__ = "digital_orders"
    id: Mapped[uuid.UUID] = uuid_pk()
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    app_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("apps.id"), index=True)
    developer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    price_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("prices.id"))
    kind: Mapped[str] = mapped_column(String(16))  # one_time / subscription
    amount_minor: Mapped[int] = mapped_column(BigInteger)  # content value excl. tax
    tax_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3))
    commission_bps: Mapped[int] = mapped_column(Integer)  # snapshot
    status: Mapped[str] = mapped_column(String(20), default="pending")
    refunded_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    locale: Mapped[str] = mapped_column(String(5))
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("subscriptions.id"))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = created_at()
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status IN ('pending','paid','failed','refunded','partially_refunded','canceled')",
                        name="status"),
        CheckConstraint("refunded_minor >= 0 AND refunded_minor <= amount_minor", name="refund_bound"),
        Index("uq_dorder_idem", "buyer_id", "idempotency_key", unique=True,
              postgresql_where="idempotency_key IS NOT NULL"),
    )


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    id: Mapped[uuid.UUID] = uuid_pk()
    context: Mapped[str] = mapped_column(String(16))  # digital / hardware
    order_id: Mapped[uuid.UUID] = mapped_column(index=True)
    provider: Mapped[str] = mapped_column(String(16))
    provider_ref: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="created")  # created/succeeded/failed/expired
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    checkout_url: Mapped[str | None] = mapped_column(Text)
    payment_ref: Mapped[str | None] = mapped_column(String(128))
    test_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("context IN ('digital','hardware')", name="context"),)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    app_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("apps.id"), index=True)
    price_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("prices.id"))
    provider: Mapped[str] = mapped_column(String(16))
    provider_ref: Mapped[str | None] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="incomplete")
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    # NAP-064: the free trial this buyer got (0 = none; a buyer gets one trial per app)
    trial_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    trial_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    periods: Mapped[list["SubscriptionPeriod"]] = relationship(order_by="SubscriptionPeriod.period_start",
                                                              lazy="selectin")
    __table_args__ = (
        CheckConstraint("status IN ('incomplete','active','past_due','canceled','ended')", name="status"),)


class SubscriptionPeriod(Base):
    """A confirmed, paid interval. Access is granted per interval; refunds touch only their own period."""
    __tablename__ = "subscription_periods"
    id: Mapped[uuid.UUID] = uuid_pk()
    subscription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("digital_orders.id"), unique=True)
    invoice_ref: Mapped[str] = mapped_column(String(128), unique=True)
    payment_ref: Mapped[str | None] = mapped_column(String(128))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="paid")  # paid / refunded / partially_refunded
    __table_args__ = (CheckConstraint("period_end > period_start", name="span"),)


class Entitlement(Base):
    __tablename__ = "entitlements"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    app_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("apps.id"), index=True)
    source: Mapped[str] = mapped_column(String(16))  # one_time / subscription / free / grant
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("digital_orders.id"), unique=True)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("subscriptions.id"), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / revoked
    revoked_reason: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("uq_free_ent", "user_id", "app_id", unique=True,
                            postgresql_where="source IN ('free','grant') AND status = 'active'"),)


class RevenueShare(Base):
    """Per-sale split, recomputed from the cumulative net to avoid rounding drift."""
    __tablename__ = "revenue_shares"
    id: Mapped[uuid.UUID] = uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("digital_orders.id"), unique=True)
    developer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    commission_bps: Mapped[int] = mapped_column(Integer)
    gross_minor: Mapped[int] = mapped_column(BigInteger)
    refunded_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    platform_minor: Mapped[int] = mapped_column(BigInteger)
    developer_minor: Mapped[int] = mapped_column(BigInteger)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class LedgerTransaction(Base):
    """Immutable, balanced journal. Corrections are reversing/adjusting transactions only."""
    __tablename__ = "ledger_transactions"
    id: Mapped[uuid.UUID] = uuid_pk()
    business_ref: Mapped[str] = mapped_column(String(160), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(3))
    memo: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = created_at()
    entries: Mapped[list["LedgerEntry"]] = relationship(lazy="selectin")


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id: Mapped[uuid.UUID] = uuid_pk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ledger_transactions.id"), index=True)
    account: Mapped[str] = mapped_column(String(120), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)  # debit > 0, credit < 0
    currency: Mapped[str] = mapped_column(String(3))
    __table_args__ = (CheckConstraint("amount_minor <> 0", name="nonzero"),)


class Payout(Base):
    __tablename__ = "payouts"
    id: Mapped[uuid.UUID] = uuid_pk()
    developer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending / paid / failed
    provider: Mapped[str] = mapped_column(String(16))
    provider_ref: Mapped[str | None] = mapped_column(String(128), unique=True)
    mock: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("amount_minor > 0", name="positive"),)


class Refund(Base):
    __tablename__ = "refunds"
    id: Mapped[uuid.UUID] = uuid_pk()
    context: Mapped[str] = mapped_column(String(16))  # digital / hardware
    order_id: Mapped[uuid.UUID] = mapped_column(index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    lines: Mapped[dict] = mapped_column(JSONB, default=dict)  # hardware: goods/shipping/fees split
    reason: Mapped[str] = mapped_column(Text, default="")
    provider_ref: Mapped[str | None] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending / succeeded / failed
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (CheckConstraint("amount_minor > 0", name="positive"),)
