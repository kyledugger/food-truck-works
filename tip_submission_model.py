from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from instant_type import UTCInstant
from store_time import utc_now
from database import Base


class TipSubmission(Base):
    __tablename__ = "tip_submissions"
    __table_args__ = (
        CheckConstraint(
            "processing_status IN ('pending', 'paid', 'rejected')",
            name="ck_tip_submissions_processing_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )

    submitted_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    store_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    store_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    report_start_at: Mapped[datetime] = mapped_column(
        UTCInstant(),
        nullable=False,
    )

    report_end_at: Mapped[datetime] = mapped_column(
        UTCInstant(),
        nullable=False,
    )

    total_tip_cents: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    payout_method: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    processing_status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
        index=True,
    )

    processed_at: Mapped[datetime | None] = mapped_column(
        UTCInstant(),
        nullable=True,
    )

    processed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    submission_data: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    submitted_at: Mapped[datetime] = mapped_column(
        UTCInstant(),
        default=utc_now,
        nullable=False,
        index=True,
    )


class TipStoreSettings(Base):
    __tablename__ = "tip_store_settings"
    __table_args__ = (
        UniqueConstraint("organization_id", "store_id", name="uq_tip_store_settings_org_store"),
        CheckConstraint("payout_policy IN ('cash', 'paycheck', 'choice')", name="ck_tip_store_payout_policy"),
        CheckConstraint("cash_confirmation IN ('self', 'authorized')", name="ck_tip_store_cash_confirmation"),
        CheckConstraint("employee_submission_hours BETWEEN 1 AND 720", name="ck_tip_employee_submission_hours"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    store_id: Mapped[str] = mapped_column(String(100), nullable=False)
    payout_policy: Mapped[str] = mapped_column(String(20), nullable=False, default="choice")
    cash_confirmation: Mapped[str] = mapped_column(String(20), nullable=False, default="authorized")
    cash_confirmer_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    tip_allocation_start_at: Mapped[datetime | None] = mapped_column(UTCInstant(), nullable=True)
    employee_submission_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)


class TipOrderClaim(Base):
    __tablename__ = "tip_order_claims"
    __table_args__ = (
        UniqueConstraint("organization_id", "poynt_business_id", "poynt_order_id", name="uq_tip_order_once"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    submission_id: Mapped[int] = mapped_column(ForeignKey("tip_submissions.id"), nullable=False, index=True)
    poynt_business_id: Mapped[str] = mapped_column(String(100), nullable=False)
    poynt_order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    store_id: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCInstant(), nullable=False)
    tip_cents: Mapped[int] = mapped_column(Integer, nullable=False)


class TipEmployeePayout(Base):
    __tablename__ = "tip_employee_payouts"
    __table_args__ = (
        UniqueConstraint("submission_id", "employee_id", name="uq_tip_payout_employee_submission"),
        CheckConstraint("payout_method IN ('cash', 'paycheck')", name="ck_tip_employee_payout_method"),
        CheckConstraint("status IN ('pending', 'paid', 'rejected')", name="ck_tip_employee_payout_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("tip_submissions.id"), nullable=False, index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False)
    employee_name: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    payout_method: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    paid_at: Mapped[datetime | None] = mapped_column(UTCInstant(), nullable=True)
    paid_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmation_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
