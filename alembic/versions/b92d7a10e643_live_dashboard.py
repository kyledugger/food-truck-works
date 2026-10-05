"""Live dashboard cache, synchronization state and durable notification inbox."""
from alembic import op
import sqlalchemy as sa

revision = "b92d7a10e643"
down_revision = "a61f0d8c3b92"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("dashboard_sync",
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("business_id", sa.String(100), nullable=False),
        *[sa.Column(name, sa.DateTime(timezone=True), nullable=name != "next_reconcile_at") for name in
          ("last_reconciled_at", "next_reconcile_at", "lease_until", "last_webhook_at")],
        sa.Column("lease_token", sa.String(64)), sa.Column("error", sa.String(200)), sa.Column("hook_id", sa.String(100)))
    op.create_table("dashboard_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("business_id", sa.String(100), nullable=False),
        sa.Column("order_id", sa.String(100), nullable=False), sa.Column("store_id", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("organization_id", "business_id", "order_id", name="uq_dashboard_order"))
    op.create_index("ix_dashboard_store_created", "dashboard_orders", ["organization_id", "business_id", "store_id", "created_at"])
    op.create_table("dashboard_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("business_id", sa.String(100), nullable=False), sa.Column("notification_id", sa.String(100), nullable=False),
        sa.Column("order_id", sa.String(100), nullable=False),
        *[sa.Column(name, sa.DateTime(timezone=True), nullable=name == "processed_at") for name in ("received_at", "retry_at", "processed_at")],
        sa.UniqueConstraint("organization_id", "business_id", "notification_id", name="uq_dashboard_notification"))
    op.create_index("ix_dashboard_pending", "dashboard_notifications", ["organization_id", "processed_at", "retry_at"])


def downgrade():
    op.drop_table("dashboard_notifications")
    op.drop_table("dashboard_orders")
    op.drop_table("dashboard_sync")
