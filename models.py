from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from instant_type import UTCInstant

from store_time import utc_now
from database import Base

import logging

logger = logging.getLogger(__name__)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)

    first_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    last_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        index=True,
        nullable=False
    )

    phone: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    email_verified_at: Mapped[datetime | None] = mapped_column(
        UTCInstant(),
        nullable=True,
    )

    pending_email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        UTCInstant(),
        default=utc_now,
        nullable=False
    )

    organization_memberships: Mapped[list["OrganizationMember"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan"
    )

    @property
    def full_name(self) -> str:
        return " ".join(
            part for part in (self.first_name, self.last_name) if part
        )

    @property
    def display_name(self) -> str:
        return self.full_name or self.email


class UserSecurityToken(Base):
    __tablename__ = "user_security_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purpose: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(UTCInstant(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCInstant(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCInstant(),
        default=utc_now,
        nullable=False,
    )

    user: Mapped["User"] = relationship("User")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        UTCInstant(),
        default=utc_now,
        nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        UTCInstant(),
        default=utc_now,
        onupdate=utc_now,
        nullable=False
    )

    members: Mapped[list["OrganizationMember"]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan"
    )

    poynt_connection: Mapped["PoyntConnection | None"] = relationship(
        back_populates="organization",
        uselist=False,
        cascade="all, delete-orphan"
    )


class OrganizationMember(Base):
    __tablename__ = "organization_members"

    id: Mapped[int] = mapped_column(primary_key=True)

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
        index=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="member"
    )

    created_at: Mapped[datetime] = mapped_column(
        UTCInstant(),
        default=utc_now,
        nullable=False
    )

    organization: Mapped["Organization"] = relationship(
        back_populates="members"
    )

    user: Mapped["User"] = relationship(
        back_populates="organization_memberships"
    )

class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_employees_organization_user",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )   

    user: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[user_id],
    )
     
    first_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    last_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="employee",
)

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        UTCInstant(),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        UTCInstant(),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    organization: Mapped["Organization"] = relationship(
        "Organization"
    )




class OrganizationInvitation(Base):
    __tablename__ = "organization_invitations"

    id: Mapped[int] = mapped_column(primary_key=True)

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )

    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"),
        nullable=False,
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        UTCInstant(),
        nullable=False,
    )

    accepted_at: Mapped[datetime | None] = mapped_column(
        UTCInstant(),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        UTCInstant(),
        default=utc_now,
        nullable=False,
    )

    invited_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    organization: Mapped["Organization"] = relationship(
        "Organization"
    )

    employee: Mapped["Employee"] = relationship(
        "Employee"
    )

    invited_by: Mapped["User"] = relationship(
        "User"
    )

class PoyntConnection(Base):
    __tablename__ = "poynt_connections"

    id: Mapped[int] = mapped_column(primary_key=True)

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"),
        unique=True,
        nullable=False,
        index=True
    )

    business_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    access_token: Mapped[str] = mapped_column(
        String(4096),
        nullable=False
    )

    refresh_token: Mapped[str | None] = mapped_column(
        String(4096),
        nullable=True
    )

    token_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        UTCInstant(),
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        UTCInstant(),
        default=utc_now,
        nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        UTCInstant(),
        default=utc_now,
        onupdate=utc_now,
        nullable=False
    )

    organization: Mapped["Organization"] = relationship(
        back_populates="poynt_connection"
    )


class OrganizationStore(Base):
    __tablename__ = "organization_stores"
    __table_args__ = (UniqueConstraint("organization_id", "store_id", name="uq_organization_store"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    store_id: Mapped[str] = mapped_column(String(100), nullable=False)
    poynt_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    timezone_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
