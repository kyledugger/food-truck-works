"""Add one primary booking resource per store, with mobile defaults.

Revision ID: 7a2d9f10c6e4
Revises: 59d6e1c8a403
"""
from alembic import op
import sqlalchemy as sa

revision = "7a2d9f10c6e4"
down_revision = "59d6e1c8a403"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "booking_resources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_store_id", sa.Integer(), sa.ForeignKey("organization_stores.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_follows_store", sa.Boolean(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.UniqueConstraint("organization_store_id", name="uq_booking_resource_store"),
        sa.CheckConstraint("capacity >= 1", name="ck_booking_resource_capacity_positive"),
    )
    op.create_index("ix_booking_resources_organization_store_id", "booking_resources", ["organization_store_id"])
    op.execute(sa.text("""
        INSERT INTO booking_resources
            (organization_store_id, name, name_follows_store, is_enabled, capacity)
        SELECT id, COALESCE(NULLIF(display_name, ''), poynt_name), true, true, 1
        FROM organization_stores
        WHERE store_type IN ('food_truck', 'food_trailer', 'cart', 'pop_up')
    """))


def downgrade():
    op.drop_index("ix_booking_resources_organization_store_id", table_name="booking_resources")
    op.drop_table("booking_resources")
