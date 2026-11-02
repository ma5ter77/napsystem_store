"""Runtime configuration. All secrets come from the environment only."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NAP_", env_file=".env", extra="ignore")

    env: Literal["demo", "test", "production"] = "demo"
    database_url: str = "postgresql+psycopg://nap:nap@localhost:5432/nap"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-insecure-change-me"  # HMAC for signed URLs, never used in production default
    public_base_url: str = "http://localhost:3000"
    # Demo accounts (*@nap.demo). Locally, in CI and in previews they share the documented password. On a public
    # server (public_demo=true) each server gets its own secret password, derived from secret_key unless
    # demo_password is set; read it on the server with `python -m app.tools.demo_password`.
    public_demo: bool = False
    demo_password: str = ""
    api_base_url: str = "http://localhost:8000"

    # storage
    storage_backend: Literal["local", "s3"] = "local"
    storage_root: str = "./var/storage"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "nap-private"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"
    signed_url_ttl_seconds: int = 300
    upload_url_ttl_seconds: int = 900

    # converter limits (shown to the author before upload)
    max_upload_bytes: int = 1024 * 1024 * 1024  # 1 GiB
    max_duration_seconds: int = 120 * 60
    job_workdir: str = "./var/jobs"
    job_timeout_seconds: int = 3 * 3600
    job_max_attempts: int = 3
    job_lease_seconds: int = 120
    job_cpu_seconds: int = 4 * 3600
    job_memory_bytes: int = 2 * 1024 * 1024 * 1024
    job_disk_reserve_bytes: int = 256 * 1024 * 1024
    preview_seconds: int = 30
    run_jobs_inline: bool = False  # tests / single-process demo without Celery

    # auth
    session_ttl_hours: int = 24 * 14
    cookie_secure: bool = False
    login_max_attempts: int = 8
    login_window_seconds: int = 900

    # e-mail (NAP-007): log (demo) / file (.eml files) / smtp
    email_backend: Literal["log", "file", "smtp"] = "log"
    email_from: str = "NAP Store <no-reply@nap.local>"
    email_reply_to: str | None = None
    email_file_dir: str = "./var/mail"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"

    # backups (NAP-006): status file written by ops/backup-loop.sh
    backup_status_file: str = "/backups/last-status.json"
    backup_max_age_hours: int = 26

    # rate limits (NAP-052); per-bucket overrides: NAP_RATE_LIMITS="checkout=10/600,global=600/60"
    rate_limits_enabled: bool = True
    rate_limits: str = ""

    # reviews (NAP-065)
    reviews_min_for_average: int = 5  # the average is shown only from this many ratings
    reviews_per_day: int = 10  # new or edited reviews per user per 24 h
    reviews_allow_reset_on_version: bool = False  # App Store-like reset; off by decision of 31.10.2026

    # billing
    # NAP-012: currencies authors may price in; buyers see their own when the author set it
    currencies: list[str] = ["EUR", "USD"]  # add "RUB" (and YooKassa keys) to sell to buyers in Russia
    payment_provider: Literal["mock", "stripe"] = "mock"
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    # NAP-020: Stripe Tax — US sales tax, EU/UK VAT, etc. computed at checkout (needs registrations in Stripe)
    stripe_automatic_tax: bool = False
    stripe_tax_id_collection: bool = False  # business buyers may enter a VAT/tax id
    stripe_api_base: str | None = None      # tests: stripe-mock (http://localhost:12111); never in production
    # NAP-012: YooKassa for buyers in Russia (RUB), online receipts under 54-FZ
    yookassa_shop_id: str | None = None
    yookassa_secret_key: str | None = None
    yookassa_vat_code: int = 1             # 1 = без НДС (УСН/патент); 4 = 20 %; 11 = 22 % — ask the accountant
    yookassa_check_ip: bool = True         # notifications only from YooKassa's published networks
    platform_commission_bps: int = 3000  # 30 %
    payout_hold_days: int = 0
    subscription_grace_days: int = 0
    default_currency: str = "EUR"

    # shipping
    shipping_adapter: Literal["mock", "manual", "easypost"] = "mock"
    easypost_api_key: str | None = None
    easypost_webhook_secret: str | None = None
    reservation_ttl_minutes: int = 30
    rate_ttl_minutes: int = 60

    # data retention
    measurement_retention_days: int = 365
    analytics_min_group: int = 5

    @field_validator("platform_commission_bps")
    @classmethod
    def _bps(cls, v: int) -> int:
        if not 0 <= v <= 10000:
            raise ValueError("commission must be 0..10000 bps")
        return v

    @model_validator(mode="after")
    def _production_guard(self) -> "Settings":
        if self.env == "production":
            if self.payment_provider == "mock":
                raise ValueError("Mock payments are not available in production")
            if self.shipping_adapter == "mock":
                raise ValueError("Mock shipping is not available in production")
            if self.secret_key.startswith("dev-"):
                raise ValueError("NAP_SECRET_KEY must be set in production")
            if self.storage_backend == "s3" and (not self.s3_secret_key or "change-me" in self.s3_secret_key):
                raise ValueError("NAP_S3_SECRET_KEY must be set in production")
            if not self.cookie_secure:
                raise ValueError("NAP_COOKIE_SECURE must be true in production")
        return self

    @property
    def demo_mode(self) -> bool:
        return self.env != "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
