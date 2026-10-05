from datetime import datetime
from sqlalchemy import ForeignKey, String, Text, JSON, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from database import Base
from instant_type import UTCInstant
from store_time import utc_now


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"
    __table_args__ = (
        UniqueConstraint("organization_id", "provider", "environment", "external_account_id", name="uq_integration_account"),
        UniqueConstraint("provider", "environment", "external_account_id", name="uq_integration_account_owner"),
        CheckConstraint("environment IN ('sandbox', 'production')", name="ck_integration_environment"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    environment: Mapped[str] = mapped_column(String(20))
    external_account_id: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="connected")
    access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    granted_scopes: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(UTCInstant())
    refreshed_at: Mapped[datetime | None] = mapped_column(UTCInstant())
    verified_at: Mapped[datetime | None] = mapped_column(UTCInstant())
    created_at: Mapped[datetime] = mapped_column(UTCInstant(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCInstant(), default=utc_now, onupdate=utc_now)


class IntegrationMapping(Base):
    __tablename__ = "integration_mappings"
    __table_args__ = (
        UniqueConstraint("connection_id", "kind", "external_id", name="uq_integration_external_mapping"),
        UniqueConstraint("connection_id", "store_id", name="uq_integration_store_mapping"),
        UniqueConstraint("connection_id", "employee_id", name="uq_integration_employee_mapping"),
        CheckConstraint("(kind = 'location' AND store_id IS NOT NULL AND employee_id IS NULL) OR (kind = 'employee' AND employee_id IS NOT NULL AND store_id IS NULL)", name="ck_integration_mapping_target"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(200))
    store_id: Mapped[int | None] = mapped_column(ForeignKey("organization_stores.id", ondelete="CASCADE"))
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))


class IntegrationOAuthAttempt(Base):
    __tablename__ = "integration_oauth_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(50))
    environment: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(UTCInstant())
    used_at: Mapped[datetime | None] = mapped_column(UTCInstant())
