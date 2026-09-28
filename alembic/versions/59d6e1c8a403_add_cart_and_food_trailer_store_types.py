"""Allow cart and food trailer as organization store types.

Revision ID: 59d6e1c8a403
Revises: 3c9f0a7e5b21
"""
from alembic import op
import sqlalchemy as sa

revision = "59d6e1c8a403"
down_revision = "3c9f0a7e5b21"
branch_labels = None
depends_on = None

CONSTRAINT = "ck_organization_stores_store_type"


def upgrade():
    op.drop_constraint(CONSTRAINT, "organization_stores", type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        "organization_stores",
        "store_type IN ('food_truck', 'food_trailer', 'cart', 'pop_up', 'shop', 'catering')",
    )


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text(
        "SELECT 1 FROM organization_stores WHERE store_type IN ('cart', 'food_trailer') LIMIT 1"
    )).first():
        raise RuntimeError("Reclassify Cart and Food trailer stores before downgrading.")
    op.drop_constraint(CONSTRAINT, "organization_stores", type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        "organization_stores",
        "store_type IN ('food_truck', 'pop_up', 'shop', 'catering')",
    )
