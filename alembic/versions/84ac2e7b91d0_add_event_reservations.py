"""Add venue-local events with UTC booking intervals.

Revision ID: 84ac2e7b91d0
Revises: 7a2d9f10c6e4
"""
from alembic import op
import sqlalchemy as sa

revision = "84ac2e7b91d0"
down_revision = "7a2d9f10c6e4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("booking_resource_id", sa.Integer(), sa.ForeignKey("booking_resources.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("venue_timezone_name", sa.String(100), nullable=False),
        sa.Column("service_start_local", sa.DateTime(timezone=False), nullable=False),
        sa.Column("service_end_local", sa.DateTime(timezone=False), nullable=False),
        sa.Column("service_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("setup_minutes", sa.Integer(), nullable=False),
        sa.Column("cleanup_minutes", sa.Integer(), nullable=False),
        sa.Column("reserved_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('confirmed', 'cancelled')", name="ck_events_status"),
        sa.CheckConstraint("service_end_at > service_start_at", name="ck_events_service_interval"),
        sa.CheckConstraint("reserved_end_at > reserved_start_at", name="ck_events_reserved_interval"),
        sa.CheckConstraint("setup_minutes >= 0 AND cleanup_minutes >= 0", name="ck_events_buffer_minutes"),
    )
    op.create_index("ix_events_organization_id", "events", ["organization_id"])
    op.create_index("ix_events_booking_resource_id", "events", ["booking_resource_id"])
    op.create_index(
        "ix_events_resource_status_interval", "events",
        ["booking_resource_id", "status", "reserved_start_at", "reserved_end_at"],
    )


def downgrade():
    op.drop_index("ix_events_resource_status_interval", table_name="events")
    op.drop_index("ix_events_booking_resource_id", table_name="events")
    op.drop_index("ix_events_organization_id", table_name="events")
    op.drop_table("events")
