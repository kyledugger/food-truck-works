"""Temporary event allocation tables for historical transaction review."""
from sqlalchemy import Column, Date, ForeignKey, ForeignKeyConstraint, Integer, String, Table

from database import Base
from instant_type import UTCInstant

allocation_events = Table(
    "allocation_events", Base.metadata,
    Column("id", Integer, primary_key=True),
    Column("organization_id", Integer, ForeignKey("organizations.id"), nullable=False),
    Column("event_date", Date, nullable=False),
    Column("name", String(200), nullable=False),
    Column("city", String(100), nullable=False),
    Column("county", String(100), nullable=False),
)

allocation_claims = Table(
    "allocation_claims", Base.metadata,
    Column("organization_id", Integer, primary_key=True),
    Column("business_id", String(100), primary_key=True),
    Column("transaction_id", String(100), primary_key=True),
    Column("event_id", Integer, ForeignKey("allocation_events.id"), nullable=False),
    Column("claimed_by", Integer, ForeignKey("users.id"), nullable=False),
    Column("claimed_at", UTCInstant(), nullable=False),
    ForeignKeyConstraint(
        ["organization_id", "business_id", "transaction_id"],
        ["poynt_transactions.organization_id", "poynt_transactions.business_id",
         "poynt_transactions.transaction_id"],
    ),
)
