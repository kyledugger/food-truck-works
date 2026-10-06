"""Authoritative pricing SKUs and latest store discovery."""
from alembic import op
import sqlalchemy as sa

revision = "f32a91c07e64"
down_revision = "a47c9e25d136"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("pricing_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("sku", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(200)),
        sa.UniqueConstraint("organization_id", "sku", name="uq_pricing_product_sku"))
    op.create_index("ix_pricing_products_organization_id", "pricing_products", ["organization_id"])
    op.create_table("product_discoveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("store_id", sa.String(100), nullable=False),
        sa.Column("business_id", sa.String(100), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("organization_id", "store_id", name="uq_product_discovery_store"))
    op.create_index("ix_product_discoveries_organization_id", "product_discoveries", ["organization_id"])


def downgrade():
    op.drop_table("product_discoveries")
    op.drop_table("pricing_products")
