"""Categorize organization stores without assuming their booking capacity.

Revision ID: 3c9f0a7e5b21
Revises: 8b7d2e9c4a61
"""
from alembic import op
import sqlalchemy as sa

revision = "3c9f0a7e5b21"
down_revision = "8b7d2e9c4a61"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("organization_stores", sa.Column("store_type", sa.String(20), nullable=True))
    op.create_check_constraint(
        "ck_organization_stores_store_type",
        "organization_stores",
        "store_type IN ('food_truck', 'pop_up', 'shop', 'catering')",
    )


def downgrade():
    op.drop_constraint("ck_organization_stores_store_type", "organization_stores", type_="check")
    op.drop_column("organization_stores", "store_type")
