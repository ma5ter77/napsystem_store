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

    # billing
    payment_provider: Literal["mock", "stripe"] = "mock"
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
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
            if not self.cookie_secure:
                raise ValueError("NAP_COOKIE_SECURE must be true in production")
        return self

    @property
    def demo_mode(self) -> bool:
        return self.env != "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
