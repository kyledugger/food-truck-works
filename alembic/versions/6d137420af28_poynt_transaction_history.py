"""Add shared Poynt transaction history."""
from alembic import op
import sqlalchemy as sa

revision = "6d137420af28"
down_revision = "c27a90e51f68"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "poynt_transactions",
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True),        
        sa.Column("business_id", sa.String(100), primary_key=True),
        sa.Column("transaction_id", sa.String(100), primary_key=True),
        sa.Column("store_id", sa.String(100)),
        sa.Column("terminal_id", sa.String(100)),
        sa.Column("parent_id", sa.String(100)),
        sa.Column("order_id", sa.String(100)),
        sa.Column("payment_type", sa.String(50)),
        sa.Column("action", sa.String(50)),
        sa.Column("status", sa.String(50)),
        sa.Column("voided", sa.Boolean),
        sa.Column("settled", sa.Boolean),
        sa.Column("currency", sa.String(10)),
        sa.Column("transaction_cents", sa.BigInteger),
        sa.Column("order_cents", sa.BigInteger),
        sa.Column("tip_cents", sa.BigInteger),
        sa.Column("tax_cents", sa.BigInteger),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_poynt_transactions_period", "poynt_transactions",
        ["organization_id", "business_id", "created_at"],
    )


def downgrade():
    op.drop_table("poynt_transactions")
