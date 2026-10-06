"""Persist store product identities independently of SKU codes."""
from alembic import op
import sqlalchemy as sa

revision = "b81e742d9c30"
down_revision = "f32a91c07e64"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("pricing_product_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("business_id", sa.String(100), nullable=False),
        sa.Column("store_id", sa.String(100), nullable=False),
        sa.Column("provider_product_id", sa.String(100), nullable=False),
        sa.Column("pricing_product_id", sa.Integer(), sa.ForeignKey("pricing_products.id"), nullable=False),
        sa.UniqueConstraint("organization_id", "business_id", "store_id", "provider_product_id", name="uq_pricing_link_provider"),
        sa.UniqueConstraint("organization_id", "business_id", "store_id", "pricing_product_id", name="uq_pricing_link_definition"))


def downgrade():
    op.drop_table("pricing_product_links")
