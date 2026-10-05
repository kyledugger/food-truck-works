"""Persistent sales cache and durable webhook inbox. Alembic owns creation."""
from datetime import datetime, date
from sqlalchemy import ForeignKey, String, JSON, UniqueConstraint, Index, Date
from sqlalchemy.orm import Mapped, mapped_column
from database import Base
from instant_type import UTCInstant
from store_time import utc_now


class DashboardSync(Base):
    __tablename__ = "dashboard_sync"
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(100))
    last_reconciled_at: Mapped[datetime | None] = mapped_column(UTCInstant())
    next_reconcile_at: Mapped[datetime] = mapped_column(UTCInstant(), default=utc_now)
    lease_until: Mapped[datetime | None] = mapped_column(UTCInstant())
    lease_token: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(String(200))
    hook_id: Mapped[str | None] = mapped_column(String(100))
    last_webhook_at: Mapped[datetime | None] = mapped_column(UTCInstant())


class DashboardOrder(Base):
    __tablename__ = "dashboard_orders"
    __table_args__ = (
        UniqueConstraint("organization_id", "business_id", "order_id", name="uq_dashboard_order"),
        Index("ix_dashboard_store_created", "organization_id", "business_id", "store_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    business_id: Mapped[str] = mapped_column(String(100))
    order_id: Mapped[str] = mapped_column(String(100))
    store_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCInstant())
    provider_updated_at: Mapped[datetime] = mapped_column(UTCInstant())
    payload: Mapped[dict] = mapped_column(JSON)


class DashboardNotification(Base):
    __tablename__ = "dashboard_notifications"
    __table_args__ = (
        UniqueConstraint("organization_id", "business_id", "notification_id", name="uq_dashboard_notification"),
        Index("ix_dashboard_pending", "organization_id", "processed_at", "retry_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    business_id: Mapped[str] = mapped_column(String(100))
    notification_id: Mapped[str] = mapped_column(String(100))
    order_id: Mapped[str] = mapped_column(String(100))
    received_at: Mapped[datetime] = mapped_column(UTCInstant(), default=utc_now)
    retry_at: Mapped[datetime] = mapped_column(UTCInstant(), default=utc_now)
    processed_at: Mapped[datetime | None] = mapped_column(UTCInstant())


class DashboardDay(Base):
    """Requested historical days, loaded once then refreshed only on demand."""
    __tablename__ = "dashboard_days"
    __table_args__ = (
        UniqueConstraint("organization_id", "business_id", "report_date", name="uq_dashboard_day"),
        Index("ix_dashboard_day_pending", "next_load_at", "requested_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    business_id: Mapped[str] = mapped_column(String(100))
    report_date: Mapped[date] = mapped_column(Date)
    loaded_at: Mapped[datetime | None] = mapped_column(UTCInstant())
    next_load_at: Mapped[datetime] = mapped_column(UTCInstant(), default=utc_now)
    requested_at: Mapped[datetime] = mapped_column(UTCInstant(), default=utc_now)
    error: Mapped[str | None] = mapped_column(String(200))
