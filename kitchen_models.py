"""Anonymous preparation state, independent of POS payment/fulfillment state."""
from datetime import datetime
from sqlalchemy import ForeignKey, String, Text, JSON, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from database import Base
from instant_type import UTCInstant
from store_time import utc_now


class KitchenTicket(Base):
    __tablename__ = "kitchen_tickets"
    __table_args__ = (UniqueConstraint("organization_id", "business_id", "order_id", name="uq_kitchen_order"),
                     Index("ix_kitchen_queue", "store_id", "business_id", "state", "created_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    store_id: Mapped[int] = mapped_column(ForeignKey("organization_stores.id", ondelete="CASCADE"))
    business_id: Mapped[str] = mapped_column(String(100))
    order_id: Mapped[str] = mapped_column(String(100))
    number: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCInstant())
    updated_at: Mapped[datetime] = mapped_column(UTCInstant(), default=utc_now)
    ready_at: Mapped[datetime | None] = mapped_column(UTCInstant())
    state: Mapped[str] = mapped_column(String(20), default="active")
    revision: Mapped[int] = mapped_column(default=1)
    items: Mapped[list] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    customer_name: Mapped[str | None] = mapped_column(Text)


class KitchenAction(Base):
    __tablename__ = "kitchen_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("kitchen_tickets.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(UTCInstant(), default=utc_now)
    action: Mapped[str] = mapped_column(String(30))
    item_key: Mapped[str | None] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column()
    # Authenticated account for audit only; claims remain anonymous in the UI.
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    details: Mapped[dict] = mapped_column(JSON)
